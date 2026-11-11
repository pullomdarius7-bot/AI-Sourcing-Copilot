import asyncio
import uuid

import pytest
from sqlalchemy import text

from app.db import SessionLocal, set_tenant
from app.jobs import queue
from app.jobs import worker as worker_module
from app.jobs.errors import PermanentError, RetryableError
from app.jobs.handlers import HANDLERS, ON_DEAD
from app.jobs.worker import Worker
from tests.helpers import add_member, register_admin
from tests.samples import QUOTE


@pytest.fixture(autouse=True)
def _restore_handlers():
    handlers, hooks = dict(HANDLERS), dict(ON_DEAD)
    yield
    HANDLERS.clear()
    HANDLERS.update(handlers)
    ON_DEAD.clear()
    ON_DEAD.update(hooks)


async def add_job(org_id, kind="test.job", payload=None, **kw):
    async with SessionLocal() as db:
        await set_tenant(db, uuid.UUID(org_id))
        job = await queue.enqueue(db, org_id=uuid.UUID(org_id), kind=kind, payload=payload or {}, **kw)
        await db.commit()
        return job


async def job_row(admin_engine, job_id):
    async with admin_engine.connect() as conn:
        return (await conn.execute(text("SELECT * FROM jobs WHERE id = :i"), {"i": job_id})).mappings().one()


async def test_successful_job_is_recorded(client, worker, admin_engine):
    admin = await register_admin(client)
    HANDLERS["test.job"] = lambda db, job: _ok({"done": True})
    job = await add_job(admin.org_id)
    assert await worker.drain() == 1
    row = await job_row(admin_engine, job.id)
    assert row["status"] == "succeeded" and row["attempts"] == 1 and row["result"] == {"done": True}


async def _ok(result):
    return result


async def test_enqueue_dedupes_active_jobs(client):
    admin = await register_admin(client)
    assert await add_job(admin.org_id, dedupe_key="k1") is not None
    assert await add_job(admin.org_id, dedupe_key="k1") is None
    assert await add_job(admin.org_id, dedupe_key="k2") is not None


async def test_retryable_failures_back_off_then_dead_letter(client, worker, admin_engine):
    admin = await register_admin(client)
    calls = []

    async def flaky(db, job):
        calls.append(job.attempts)
        raise RetryableError("provider down")

    HANDLERS["test.job"] = flaky
    job = await add_job(admin.org_id, max_attempts=3)
    await worker.drain()
    assert calls == [1, 2, 3]
    row = await job_row(admin_engine, job.id)
    assert row["status"] == "dead" and row["attempts"] == 3 and "provider down" in row["last_error"]
    assert row["finished_at"] is not None


async def test_backoff_delays_the_next_attempt(client, worker, admin_engine, monkeypatch):
    admin = await register_admin(client)
    monkeypatch.setattr(queue.settings, "job_backoff_base_seconds", 60)

    async def fail(db, job):
        raise RuntimeError("transient")

    HANDLERS["test.job"] = fail
    job = await add_job(admin.org_id)
    assert await worker.drain() == 1  # not picked up again: run_at is ~60s away
    row = await job_row(admin_engine, job.id)
    assert row["status"] == "queued" and row["attempts"] == 1 and row["run_at"] > row["started_at"]


async def test_permanent_error_skips_retries(client, worker, admin_engine):
    admin = await register_admin(client)

    async def bad(db, job):
        raise PermanentError("cannot work")

    HANDLERS["test.job"] = bad
    job = await add_job(admin.org_id)
    await worker.drain()
    row = await job_row(admin_engine, job.id)
    assert row["status"] == "dead" and row["attempts"] == 1


async def test_unknown_job_kind_is_dead_lettered(client, worker, admin_engine):
    admin = await register_admin(client)
    job = await add_job(admin.org_id, kind="nobody.handles.this")
    await worker.drain()
    row = await job_row(admin_engine, job.id)
    assert row["status"] == "dead" and "No handler" in row["last_error"]


async def test_handler_timeout_counts_as_failure(client, worker, admin_engine, monkeypatch):
    admin = await register_admin(client)
    monkeypatch.setattr(worker_module.settings, "job_timeout_seconds", 0.05)

    async def slow(db, job):
        await asyncio.sleep(1)

    HANDLERS["test.job"] = slow
    job = await add_job(admin.org_id, max_attempts=1)
    await worker.drain()
    row = await job_row(admin_engine, job.id)
    assert row["status"] == "dead" and "Timed out" in row["last_error"]


async def test_dead_letter_hook_runs(client, worker):
    admin = await register_admin(client)
    seen = []

    async def bad(db, job):
        raise PermanentError("nope")

    async def hook(db, job, error):
        seen.append((job.kind, error))

    HANDLERS["test.job"], ON_DEAD["test.job"] = bad, hook
    await add_job(admin.org_id)
    await worker.drain()
    assert seen == [("test.job", "nope")]


async def test_concurrent_workers_never_run_a_job_twice(client):
    admin = await register_admin(client)
    runs: list[str] = []

    async def record(db, job):
        runs.append(str(job.id))
        await asyncio.sleep(0.01)

    HANDLERS["test.job"] = record
    for _ in range(12):
        await add_job(admin.org_id)
    workers = [Worker(f"w{i}") for i in range(4)]
    await asyncio.gather(*(w.drain() for w in workers))
    assert len(runs) == 12 and len(set(runs)) == 12


async def test_claim_skips_running_and_future_jobs(client, admin_engine):
    admin = await register_admin(client)
    await add_job(admin.org_id, delay_seconds=3600)
    async with SessionLocal() as db:
        assert await queue.claim(db, "w") is None
    job = await add_job(admin.org_id)
    async with SessionLocal() as db:
        claimed = await queue.claim(db, "w1")
    assert claimed.id == job.id and claimed.status == "running" and claimed.attempts == 1
    async with SessionLocal() as db:
        assert await queue.claim(db, "w2") is None


@pytest.mark.parametrize("attempts,max_attempts,expected", [(1, 3, "queued"), (3, 3, "dead")])
async def test_stale_running_jobs_are_reaped(client, admin_engine, attempts, max_attempts, expected):
    admin = await register_admin(client)
    job = await add_job(admin.org_id, max_attempts=max_attempts)
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("UPDATE jobs SET status='running', attempts=:a, locked_by='ghost', locked_until=now() - interval '1 minute' WHERE id=:i"),
            {"a": attempts, "i": job.id},
        )
    async with SessionLocal() as db:
        outcomes = await queue.reap_stale(db)
    assert outcomes == [(job.id, "retry" if expected == "queued" else "dead")]
    row = await job_row(admin_engine, job.id)
    assert row["status"] == expected and row["locked_by"] is None and "Worker lost" in row["last_error"]


async def test_old_succeeded_jobs_are_pruned(client, admin_engine):
    admin = await register_admin(client)
    job = await add_job(admin.org_id)
    async with admin_engine.begin() as conn:
        await conn.execute(text("UPDATE jobs SET status='succeeded', finished_at = now() - interval '30 days' WHERE id=:i"), {"i": job.id})
    async with SessionLocal() as db:
        assert await queue.prune_finished(db) == 1


# --- Monitoring API ------------------------------------------------------------------------
async def test_monitoring_endpoints_and_dlq_retry(client, worker):
    admin = await register_admin(client)
    broken = (await client.post("/documents", files={"file": ("bad.pdf", b"%PDF-1.4\nbroken")}, headers=admin.headers)).json()["document"]
    await client.post("/documents", files={"file": ("ok.txt", QUOTE.encode())}, headers=admin.headers)
    await worker.drain()

    stats = (await client.get("/jobs/stats", headers=admin.headers)).json()
    assert stats["succeeded"] == 4 and stats["dead"] == 1 and stats["queued"] == 0
    assert stats["succeeded_last_hour"] == 4 and stats["avg_duration_ms_last_hour"] is not None
    assert {"kind": "document.extract", "status": "dead", "count": 1} in stats["by_kind"]

    dead = (await client.get("/jobs?status=dead", headers=admin.headers)).json()
    assert len(dead) == 1 and dead[0]["kind"] == "document.extract" and "Unreadable PDF" in dead[0]["last_error"]
    assert len((await client.get("/jobs?kind=document.scan", headers=admin.headers)).json()) == 2

    assert (await client.post(f"/jobs/{dead[0]['id']}/retry", headers=admin.headers)).json()["status"] == "queued"
    doc = (await client.get(f"/documents/{broken['id']}", headers=admin.headers)).json()
    assert doc["status"] == "extracting" and doc["error"] is None
    await worker.drain()
    assert (await client.get("/jobs/stats", headers=admin.headers)).json()["dead"] == 1  # dead again (still corrupt)


async def test_only_dead_jobs_can_be_retried_and_unknown_is_404(client):
    admin = await register_admin(client)
    job = await add_job(admin.org_id)
    assert (await client.post(f"/jobs/{job.id}/retry", headers=admin.headers)).status_code == 409
    assert (await client.post(f"/jobs/{uuid.uuid4()}/retry", headers=admin.headers)).status_code == 404


async def test_job_endpoints_are_admin_only_and_tenant_scoped(client):
    a = await register_admin(client, email="a@alpha.test", org="Alpha")
    b = await register_admin(client, email="b@beta.test", org="Beta")
    job = await add_job(a.org_id)
    buyer = await add_member(client, a, "buyer@alpha.test", "buyer")
    for path in ("/jobs", "/jobs/stats", "/ai/providers"):
        assert (await client.get(path, headers=buyer.headers)).status_code == 403
    assert (await client.post(f"/jobs/{job.id}/retry", headers=buyer.headers)).status_code == 403
    assert (await client.get("/jobs", headers=b.headers)).json() == []
    assert (await client.post(f"/jobs/{job.id}/retry", headers=b.headers)).status_code == 404
    assert (await client.get("/jobs/stats", headers=b.headers)).json()["queued"] == 0


# --- Heartbeat (container health check) --------------------------------------------------------------------------
async def test_running_worker_keeps_its_heartbeat_fresh(tmp_path, monkeypatch):
    beat = tmp_path / "heartbeat"
    monkeypatch.setattr(worker_module.settings, "worker_heartbeat_file", str(beat))
    monkeypatch.setattr(worker_module.settings, "worker_heartbeat_seconds", 0.05)
    assert worker_module.healthcheck() == 1  # never started
    stop = asyncio.Event()
    task = asyncio.create_task(Worker().run_forever(stop))
    for _ in range(100):
        if beat.exists():
            break
        await asyncio.sleep(0.02)
    assert worker_module.healthcheck() == 0
    stop.set()
    await asyncio.wait_for(task, timeout=10)


def test_stale_heartbeat_is_unhealthy(tmp_path, monkeypatch):
    import os
    import time

    beat = tmp_path / "heartbeat"
    monkeypatch.setattr(worker_module.settings, "worker_heartbeat_file", str(beat))
    worker_module.touch_heartbeat()
    assert worker_module.healthcheck(max_age_seconds=60) == 0
    old = time.time() - 120
    os.utime(beat, (old, old))
    assert worker_module.healthcheck(max_age_seconds=60) == 1
