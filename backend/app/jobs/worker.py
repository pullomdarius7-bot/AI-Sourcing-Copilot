"""Native job worker: `python -m app.worker`. Several workers can run side by side."""
import asyncio
import logging
import os
import signal
import socket
import uuid

from app.config import get_settings
from app.db import SessionLocal, set_tenant
from app.jobs import queue
from app.jobs.errors import PermanentError
from app.jobs.handlers import HANDLERS, ON_DEAD

logger = logging.getLogger("app.worker")
settings = get_settings()


class Worker:
    def __init__(self, worker_id: str | None = None) -> None:
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"

    async def run_once(self) -> bool:
        """Claim and execute one job. Returns False when the queue is empty."""
        async with SessionLocal() as db:
            job = await queue.claim(db, self.worker_id)
        if job is None:
            return False
        await self._execute(job)
        return True

    async def _execute(self, job) -> None:
        handler = HANDLERS.get(job.kind)
        error: str | None = None
        retryable = True
        result = None
        if handler is None:
            error, retryable = f"No handler registered for job kind '{job.kind}'", False
        else:
            try:
                async with SessionLocal() as db:
                    await set_tenant(db, job.organization_id)
                    result = await asyncio.wait_for(handler(db, job), timeout=settings.job_timeout_seconds)
            except PermanentError as exc:
                error, retryable = str(exc), False
            except TimeoutError:
                error = f"Timed out after {settings.job_timeout_seconds}s"
            except Exception as exc:
                logger.exception("job %s (%s) failed", job.id, job.kind)
                error = f"{type(exc).__name__}: {exc}"
        async with SessionLocal() as db:
            if error is None:
                await queue.complete(db, job.id, result)
                logger.info("job %s (%s) succeeded", job.id, job.kind)
                return
            outcome = await queue.fail(db, job.id, error, retryable=retryable)
        logger.warning("job %s (%s) attempt %s failed -> %s: %s", job.id, job.kind, job.attempts, outcome, error)
        if outcome == "dead":
            await self._dead(job, error)

    async def _dead(self, job, error: str) -> None:
        hook = ON_DEAD.get(job.kind)
        if hook is None:
            return
        try:
            async with SessionLocal() as db:
                await set_tenant(db, job.organization_id)
                await hook(db, job, error)
        except Exception:
            logger.exception("dead-letter hook failed for job %s", job.id)

    async def maintenance(self) -> None:
        async with SessionLocal() as db:
            for job_id, outcome in await queue.reap_stale(db):
                logger.warning("reaped stale job %s -> %s", job_id, outcome)
                if outcome == "dead":
                    await self._dead_by_id(job_id)
        async with SessionLocal() as db:
            await queue.prune_finished(db)

    async def _dead_by_id(self, job_id: uuid.UUID) -> None:
        from app.db import set_system
        from app.models import Job

        async with SessionLocal() as db:
            await set_system(db)
            job = await db.get(Job, job_id)
        if job is not None:
            await self._dead(job, job.last_error or "Worker lost")

    async def drain(self, max_jobs: int = 200) -> int:
        """Process jobs until none are due (used by tests and one-shot runs)."""
        done = 0
        while done < max_jobs and await self.run_once():
            done += 1
        return done

    async def run_forever(self, stop: asyncio.Event) -> None:
        async def loop(n: int) -> None:
            while not stop.is_set():
                try:
                    if not await self.run_once():
                        try:
                            await asyncio.wait_for(stop.wait(), timeout=settings.job_poll_interval_seconds)
                        except TimeoutError:
                            pass
                except Exception:
                    logger.exception("worker loop %s error", n)
                    await asyncio.sleep(2)

        async def janitor() -> None:
            while not stop.is_set():
                try:
                    await self.maintenance()
                except Exception:
                    logger.exception("maintenance error")
                try:
                    await asyncio.wait_for(stop.wait(), timeout=60)
                except TimeoutError:
                    pass

        logger.info("worker %s started (concurrency=%s)", self.worker_id, settings.worker_concurrency)
        await asyncio.gather(janitor(), *(loop(i) for i in range(settings.worker_concurrency)))
        logger.info("worker %s stopped", self.worker_id)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await Worker().run_forever(stop)
