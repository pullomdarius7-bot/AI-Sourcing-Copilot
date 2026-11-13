"""Stopping a job: from the jobs page and the back office, for queued and running jobs."""
import asyncio
import uuid

import pytest
from sqlalchemy import text

from app.ai import gateway
from app.billing.plans import DEEP_ANALYSIS
from app.config import get_settings
from app.db import SessionLocal
from app.jobs import queue
from app.jobs import worker as worker_module
from app.jobs.handlers import HANDLERS, ON_DEAD
from app.ops import _set_admin
from tests.helpers import add_member, register_admin
from tests.test_jobs import add_job, job_row

settings = get_settings()


@pytest.fixture(autouse=True)
def _fast_and_isolated(monkeypatch):
    monkeypatch.setattr(worker_module.settings, "job_cancel_poll_seconds", 0.05)
    monkeypatch.setattr(settings, "platform_admin_requires_mfa", False)
    handlers, hooks = dict(HANDLERS), dict(ON_DEAD)
    yield
    HANDLERS.clear(), HANDLERS.update(handlers), ON_DEAD.clear(), ON_DEAD.update(hooks)


async def balance(client, account):
    return (await client.get("/auth/me", headers=account.headers)).json()["credit_balance"]


async def wait_for(condition, seconds=5.0):
    for _ in range(int(seconds / 0.02)):
        if await condition():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition never became true")


async def test_a_running_job_stops_within_seconds_refunds_and_can_be_retried(client, worker, admin_engine):
    admin = await register_admin(client)
    before = await balance(client, admin)
    subject = uuid.uuid4()
    started, interrupted, cleaned = asyncio.Event(), [], []

    async def slow_ai(db, job):  # holds credits, then waits on a "model" that never answers
        await gateway.acquire(db, job.organization_id, DEEP_ANALYSIS, subject)
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            interrupted.append(True)
            raise

    async def hook(db, job, note):
        cleaned.append(note)

    HANDLERS["test.job"], ON_DEAD["test.job"] = slow_ai, hook
    job = await add_job(admin.org_id, payload={"run_id": str(subject)})
    running = asyncio.create_task(worker.run_once())
    await asyncio.wait_for(started.wait(), 5)
    assert await balance(client, admin) < before  # the charge is held while the model works

    res = await client.post(f"/jobs/{job.id}/cancel", headers=admin.headers)
    assert res.status_code == 200 and res.json()["status"] == "cancelled"
    await asyncio.wait_for(running, 5)  # the worker lets go within a few poll intervals, not after 60 s

    row = await job_row(admin_engine, job.id)
    assert row["status"] == "cancelled" and row["last_error"] == "Stopped by Ada Admin"
    assert interrupted == [True] and cleaned == ["Stopped by Ada Admin"]
    assert await balance(client, admin) == before  # the held credits came back

    jobs = (await client.get("/jobs?status=cancelled", headers=admin.headers)).json()
    assert [j["id"] for j in jobs] == [str(job.id)]
    assert (await client.get("/jobs/stats", headers=admin.headers)).json()["cancelled"] == 1
    again = await client.post(f"/jobs/{job.id}/retry", headers=admin.headers)
    assert again.status_code == 200 and again.json()["status"] == "queued"


async def test_a_queued_job_never_starts(client, worker, admin_engine):
    admin = await register_admin(client)
    ran, cleaned = [], []

    async def handler(db, job):
        ran.append(job.id)

    async def hook(db, job, note):
        cleaned.append(note)

    HANDLERS["test.job"], ON_DEAD["test.job"] = handler, hook
    job = await add_job(admin.org_id)
    res = await client.post(f"/jobs/{job.id}/cancel", headers=admin.headers)
    assert res.status_code == 200 and res.json()["status"] == "cancelled"
    assert cleaned == ["Stopped by Ada Admin"]
    assert await worker.drain() == 0 and ran == []


async def test_only_queued_or_running_jobs_can_be_stopped(client, worker):
    admin = await register_admin(client)

    async def ok(db, job):
        return {"done": True}

    HANDLERS["test.job"] = ok
    job = await add_job(admin.org_id)
    await worker.drain()
    assert (await client.post(f"/jobs/{job.id}/cancel", headers=admin.headers)).status_code == 409
    assert (await client.post(f"/jobs/{uuid.uuid4()}/cancel", headers=admin.headers)).status_code == 404


async def test_a_result_arriving_after_stop_does_not_undo_it(client, admin_engine):
    admin = await register_admin(client)
    job = await add_job(admin.org_id)
    async with SessionLocal() as db:
        claimed = await queue.claim(db, "w1")
    assert claimed.id == job.id
    await client.post(f"/jobs/{job.id}/cancel", headers=admin.headers)
    async with SessionLocal() as db:
        await queue.complete(db, job.id, {"late": True})
    async with SessionLocal() as db:
        assert await queue.fail(db, job.id, "late error", retryable=True) == "cancelled"
    row = await job_row(admin_engine, job.id)
    assert row["status"] == "cancelled" and row["result"] is None


async def test_stopping_needs_the_manage_permission_and_stays_in_the_account(client):
    admin = await register_admin(client)
    viewer = await add_member(client, admin, "buyer@acme.test", "buyer")
    other = await register_admin(client, email="b@other.test", org="Other")
    job = await add_job(admin.org_id)
    assert (await client.post(f"/jobs/{job.id}/cancel", headers=viewer.headers)).status_code == 403
    assert (await client.post(f"/jobs/{job.id}/cancel", headers=other.headers)).status_code == 404


async def test_the_platform_owner_can_stop_any_accounts_job(client, admin_engine):
    shop = await register_admin(client, email="a@one.test", org="One Resale")
    owner = await register_admin(client, email="owner@platform.test", org="Platform HQ")
    assert await _set_admin("owner@platform.test", True) == 0
    job = await add_job(shop.org_id)
    assert (await client.post(f"/platform/jobs/{job.id}/cancel", json={"note": "x"}, headers=shop.headers)).status_code == 404
    res = await client.post(f"/platform/jobs/{job.id}/cancel", json={"note": "Runaway local model"}, headers=owner.headers)
    assert res.status_code == 200 and res.json()["status"] == "cancelled"
    row = await job_row(admin_engine, job.id)
    assert row["last_error"] == "Stopped by the platform team"  # the customer never sees who or why
    stopped = (await client.get("/platform/jobs", params={"status": "cancelled"}, headers=owner.headers)).json()
    assert [j["id"] for j in stopped] == [str(job.id)]
    audit = (await client.get("/platform/audit", headers=owner.headers)).json()
    assert audit[0]["action"] == "job.cancelled" and audit[0]["meta"]["note"] == "Runaway local model"
    retried = await client.post(f"/platform/jobs/{job.id}/retry", json={"note": "Fixed"}, headers=owner.headers)
    assert retried.status_code == 200 and retried.json()["status"] == "queued"
    async with admin_engine.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM jobs WHERE status = 'queued'"))).scalar() == 1
