import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.service import get_ai_service
from app.audit import record
from app.db import get_db
from app.deps import Principal, require_permission
from app.errors import conflict, not_found
from app.jobs import control, queue
from app.jobs.handlers import ANALYZE, EXTRACT, SCAN
from app.models import AiAuditLog, Document, Job
from app.rbac import Permission
from app.schemas import AiAuditOut, JobOut, JobStatsOut, ProviderOut
from app.services.auth import utcnow

router = APIRouter(tags=["jobs"])

_RESTORE_STATUS = {SCAN: "uploaded", EXTRACT: "extracting", ANALYZE: "analyzing"}


@router.get("/jobs", response_model=list[JobOut])
async def list_jobs(
    status: str | None = Query(None, pattern="^(queued|running|succeeded|dead|cancelled)$"),
    kind: str | None = Query(None, max_length=60),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    p: Principal = Depends(require_permission(Permission.JOBS_READ)),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Job).where(Job.organization_id == p.org_id)
    if status:
        stmt = stmt.where(Job.status == status)
    if kind:
        stmt = stmt.where(Job.kind == kind)
    rows = await db.scalars(stmt.order_by(Job.created_at.desc()).limit(limit).offset(offset))
    return list(rows)


@router.get("/jobs/stats", response_model=JobStatsOut)
async def job_stats(
    p: Principal = Depends(require_permission(Permission.JOBS_READ)), db: AsyncSession = Depends(get_db)
):
    return await queue.stats(db, p.org_id)


@router.post("/jobs/{job_id}/retry", response_model=JobOut)
async def retry_job(
    job_id: uuid.UUID,
    p: Principal = Depends(require_permission(Permission.JOBS_MANAGE)),
    db: AsyncSession = Depends(get_db),
):
    job = await db.scalar(select(Job).where(Job.id == job_id, Job.organization_id == p.org_id).with_for_update())
    if job is None:
        raise not_found("Job not found")
    if job.status not in queue.RETRYABLE:
        raise conflict("not_dead", "Only failed or stopped jobs can be retried")
    clash = None
    if job.dedupe_key:
        clash = await db.scalar(
            select(Job.id).where(Job.dedupe_key == job.dedupe_key, Job.status.in_(("queued", "running")))
        )
    if clash:
        raise conflict("already_active", "An identical job is already queued")
    await queue.retry_dead(db, job)
    doc_id = job.payload.get("document_id")
    if doc_id and job.kind in _RESTORE_STATUS:
        doc = await db.get(Document, uuid.UUID(doc_id))
        if doc is not None and doc.status != "quarantined":
            doc.status, doc.error, doc.updated_at = _RESTORE_STATUS[job.kind], None, utcnow()
            if job.kind == ANALYZE:
                doc.analysis_status = "pending"
    record(db, org_id=p.org_id, user_id=p.user.id, action="job.retried", ip=p.ip, meta={"job_id": str(job.id), "kind": job.kind})
    await db.commit()
    return job


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
async def cancel_job(
    job_id: uuid.UUID,
    p: Principal = Depends(require_permission(Permission.JOBS_MANAGE)),
    db: AsyncSession = Depends(get_db),
):
    """Stop a queued or running job. A running one stops within a few seconds; Retry starts it again."""
    job = await db.scalar(select(Job).where(Job.id == job_id, Job.organization_id == p.org_id).with_for_update())
    if job is None:
        raise not_found("Job not found")
    if job.status not in queue.STOPPABLE:
        raise conflict("not_running", "Only queued or running jobs can be stopped")
    previous = queue.cancel(job, f"Stopped by {p.user.full_name}")
    record(db, org_id=p.org_id, user_id=p.user.id, action="job.cancelled", ip=p.ip,
           meta={"job_id": str(job.id), "kind": job.kind, "was": previous})
    await db.commit()
    if previous == "queued":
        await control.after_stop(db, job, job.last_error or "Stopped")
    await db.refresh(job)
    return job


@router.get("/ai/providers", response_model=list[ProviderOut])
async def ai_providers(_: Principal = Depends(require_permission(Permission.JOBS_READ))):
    return [ProviderOut(name=pr.name, model=pr.model, external=pr.external) for pr in get_ai_service().providers]


@router.get("/ai/audit", response_model=list[AiAuditOut])
async def ai_audit(
    document_id: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    p: Principal = Depends(require_permission(Permission.AUDIT_READ)),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(AiAuditLog).where(AiAuditLog.organization_id == p.org_id)
    if document_id:
        stmt = stmt.where(AiAuditLog.document_id == document_id)
    rows = await db.scalars(stmt.order_by(AiAuditLog.id.desc()).limit(limit).offset(offset))
    return list(rows)
