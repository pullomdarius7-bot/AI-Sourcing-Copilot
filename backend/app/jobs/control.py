"""Stopping a job that never started: the same clean-up a worker does after interrupting a running one."""
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import gateway
from app.jobs import queue
from app.models import Job

logger = logging.getLogger("app.jobs")


async def after_stop(db: AsyncSession, job: Job, note: str) -> None:
    """Refund credits still held for the job's records (from an earlier interrupted attempt) and mark its
    document or extraction as stopped. `db` is scoped to the job's organization; the stop is already committed."""
    if await gateway.release_held(db, job.organization_id, queue.subjects(job)):
        await db.commit()
    from app.jobs.handlers import ON_DEAD

    hook = ON_DEAD.get(job.kind)
    if hook is None:
        return
    try:
        await hook(db, job, note)
    except Exception:
        logger.exception("clean-up after stopping job %s failed", job.id)
        await db.rollback()
