"""PostgreSQL-backed job queue: SKIP LOCKED claiming, retries with backoff, dead-letter state."""
import random
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import set_system
from app.models import Job

settings = get_settings()


def utcnow() -> datetime:
    return datetime.now(UTC)


def backoff_seconds(attempts: int) -> float:
    base = settings.job_backoff_base_seconds
    delay = min(base * (2 ** max(attempts - 1, 0)), settings.job_backoff_max_seconds)
    return delay * random.uniform(0.8, 1.2) if delay else 0.0


PRIORITY_TEAM = 10


async def enqueue(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    kind: str,
    payload: dict[str, Any],
    dedupe_key: str | None = None,
    max_attempts: int | None = None,
    delay_seconds: float = 0,
) -> Job | None:
    """Add a job inside the caller's transaction (transactional outbox). None if an identical job is active.
    Sourcing Team accounts get priority processing: their jobs are claimed before everyone else's."""
    from app.billing.plans import TEAM_PLAN
    from app.models import Organization

    plan = await db.scalar(select(Organization.plan).where(Organization.id == org_id))
    job = Job(
        priority=PRIORITY_TEAM if plan == TEAM_PLAN else 0,
        organization_id=org_id,
        kind=kind,
        payload=payload,
        dedupe_key=dedupe_key,
        max_attempts=max_attempts or settings.job_max_attempts,
        run_at=utcnow() + timedelta(seconds=delay_seconds),
    )
    try:
        async with db.begin_nested():
            db.add(job)
            await db.flush()
    except IntegrityError:
        return None
    return job


async def claim(db: AsyncSession, worker_id: str) -> Job | None:
    await set_system(db)
    now = utcnow()
    job = await db.scalar(
        select(Job)
        .where(Job.status == "queued", Job.run_at <= now)
        .order_by(Job.priority.desc(), Job.run_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        await db.rollback()
        return None
    job.status = "running"
    job.attempts += 1
    job.locked_by = worker_id
    job.started_at = now
    job.locked_until = now + timedelta(seconds=settings.job_visibility_seconds)
    await db.commit()
    return job


async def complete(db: AsyncSession, job_id: uuid.UUID, result: dict[str, Any] | None) -> None:
    await set_system(db)
    job = await db.get(Job, job_id, with_for_update=True)
    if job is None:
        return
    job.status = "succeeded"
    job.result = result
    job.last_error = None
    job.finished_at = utcnow()
    job.locked_by = None
    job.locked_until = None
    await db.commit()


async def fail(db: AsyncSession, job_id: uuid.UUID, error: str, *, retryable: bool) -> str:
    """Record a failed attempt. Returns 'retry' or 'dead'."""
    await set_system(db)
    job = await db.get(Job, job_id, with_for_update=True)
    if job is None:
        return "dead"
    job.last_error = error[:4000]
    job.locked_by = None
    job.locked_until = None
    if not retryable or job.attempts >= job.max_attempts:
        job.status = "dead"
        job.finished_at = utcnow()
        outcome = "dead"
    else:
        job.status = "queued"
        job.run_at = utcnow() + timedelta(seconds=backoff_seconds(job.attempts))
        outcome = "retry"
    await db.commit()
    return outcome


async def reap_stale(db: AsyncSession) -> list[tuple[uuid.UUID, str]]:
    """Jobs whose worker vanished (lock expired) count as a failed attempt."""
    await set_system(db)
    stale = (
        await db.scalars(
            select(Job)
            .where(Job.status == "running", Job.locked_until < utcnow())
            .with_for_update(skip_locked=True)
        )
    ).all()
    outcomes: list[tuple[uuid.UUID, str]] = []
    for job in stale:
        job.last_error = "Worker lost: visibility timeout expired"
        job.locked_by = None
        job.locked_until = None
        if job.attempts >= job.max_attempts:
            job.status = "dead"
            job.finished_at = utcnow()
            outcomes.append((job.id, "dead"))
        else:
            job.status = "queued"
            job.run_at = utcnow() + timedelta(seconds=backoff_seconds(job.attempts))
            outcomes.append((job.id, "retry"))
    await db.commit()
    return outcomes


async def prune_finished(db: AsyncSession) -> int:
    await set_system(db)
    cutoff = utcnow() - timedelta(days=settings.job_retention_days)
    result = await db.execute(
        delete(Job).where(Job.status == "succeeded", Job.finished_at < cutoff)
    )
    await db.commit()
    return result.rowcount or 0


async def retry_dead(db: AsyncSession, job: Job) -> None:
    """Move a dead job back to the queue with a fresh attempt budget (caller commits)."""
    job.status = "queued"
    job.attempts = 0
    job.run_at = utcnow()
    job.finished_at = None
    job.locked_by = None
    job.locked_until = None


async def stats(db: AsyncSession, org_id: uuid.UUID) -> dict[str, Any]:
    by_status = dict(
        (await db.execute(select(Job.status, func.count()).where(Job.organization_id == org_id).group_by(Job.status))).all()
    )
    by_kind = [
        {"kind": k, "status": s, "count": c}
        for k, s, c in (
            await db.execute(
                select(Job.kind, Job.status, func.count())
                .where(Job.organization_id == org_id)
                .group_by(Job.kind, Job.status)
                .order_by(Job.kind)
            )
        ).all()
    ]
    oldest = await db.scalar(
        select(func.min(Job.run_at)).where(Job.organization_id == org_id, Job.status == "queued")
    )
    hour_ago = utcnow() - timedelta(hours=1)
    finished = await db.scalar(
        select(func.count()).where(
            Job.organization_id == org_id, Job.status == "succeeded", Job.finished_at > hour_ago
        )
    )
    avg_ms = await db.scalar(
        select(func.avg(func.extract("epoch", Job.finished_at - Job.started_at) * 1000)).where(
            Job.organization_id == org_id, Job.status == "succeeded", Job.finished_at > hour_ago
        )
    )
    workers = (
        await db.scalars(
            select(Job.locked_by).where(
                Job.organization_id == org_id, Job.status == "running", Job.locked_by.is_not(None)
            ).distinct()
        )
    ).all()
    return {
        "queued": by_status.get("queued", 0),
        "running": by_status.get("running", 0),
        "succeeded": by_status.get("succeeded", 0),
        "dead": by_status.get("dead", 0),
        "oldest_queued_age_seconds": max(0, int((utcnow() - oldest).total_seconds())) if oldest else None,
        "succeeded_last_hour": finished or 0,
        "avg_duration_ms_last_hour": int(avg_ms) if avg_ms is not None else None,
        "active_workers": sorted(workers),
        "by_kind": by_kind,
    }
