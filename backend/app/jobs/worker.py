"""Native job worker: `python -m app.worker`. Several workers can run side by side."""
import asyncio
import logging
import os
import signal
import socket
import time
import uuid

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal, set_tenant
from app.jobs import queue
from app.jobs.errors import PermanentError
from app.jobs.handlers import HANDLERS, ON_DEAD

logger = logging.getLogger("app.worker")


class JobStopped(Exception):
    """Someone pressed Stop on the job while it ran."""
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
                result = await self._run(handler, job)
            except JobStopped:
                logger.info("job %s (%s) stopped by a user", job.id, job.kind)
                await self._stopped(job)
                return
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
        if outcome == "cancelled":
            return
        logger.warning("job %s (%s) attempt %s failed -> %s: %s", job.id, job.kind, job.attempts, outcome, error)
        if outcome == "dead":
            await self._dead(job, error)

    async def _run(self, handler, job):
        """Run the handler, checking every few seconds whether someone pressed Stop (and enforcing the timeout)."""
        async def work():
            async with SessionLocal() as db:
                await set_tenant(db, job.organization_id)
                return await handler(db, job)

        task = asyncio.create_task(work())
        loop = asyncio.get_running_loop()
        deadline = loop.time() + settings.job_timeout_seconds
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError
                done, _ = await asyncio.wait({task}, timeout=min(settings.job_cancel_poll_seconds, remaining))
                if task in done:
                    return task.result()
                if await queue.is_cancelled(job.id):
                    raise JobStopped
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def _stopped(self, job) -> None:
        """After an interrupted job: give back credits it still held and mark its document or run as stopped."""
        from app.ai import gateway
        from app.db import set_system
        from app.models import Job

        async with SessionLocal() as db:
            await set_tenant(db, job.organization_id)
            if await gateway.release_held(db, job.organization_id, queue.subjects(job)):
                await db.commit()
        async with SessionLocal() as db:
            await set_system(db)
            note = await db.scalar(select(Job.last_error).where(Job.id == job.id))
        await self._dead(job, note or "Stopped")

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

        async def heartbeat() -> None:
            # Runs beside the job loops, so a long job does not make the worker look unhealthy; a crashed or frozen
            # worker stops touching the file and the container health check fails.
            while not stop.is_set():
                try:
                    touch_heartbeat()
                except OSError:
                    logger.exception("cannot write heartbeat file %s", settings.worker_heartbeat_file)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=settings.worker_heartbeat_seconds)
                except TimeoutError:
                    pass

        logger.info("worker %s started (concurrency=%s)", self.worker_id, settings.worker_concurrency)
        await asyncio.gather(heartbeat(), janitor(), *(loop(i) for i in range(settings.worker_concurrency)))
        logger.info("worker %s stopped", self.worker_id)


def touch_heartbeat() -> None:
    with open(settings.worker_heartbeat_file, "a"):
        pass
    os.utime(settings.worker_heartbeat_file)


def heartbeat_age() -> float | None:
    """Seconds since the worker last touched its heartbeat file, or None if it never has."""
    try:
        return time.time() - os.path.getmtime(settings.worker_heartbeat_file)
    except OSError:
        return None


def healthcheck(max_age_seconds: float = 60) -> int:
    """Exit status for the container health check: 0 while the heartbeat is fresh."""
    age = heartbeat_age()
    return 0 if age is not None and age < max_age_seconds else 1


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await Worker().run_forever(stop)
