"""The platform owner's back office: access, health checks and audited manual fixes."""
import pytest
from sqlalchemy import text

from app import email as mail
from app.config import get_settings
from app.ops import _health, _set_admin
from tests.helpers import register_admin
from tests.test_sourcing import fake_web, invite_mail, new_request  # noqa: F401 - fixture
from tests.market_helpers import buyer

settings = get_settings()


@pytest.fixture(autouse=True)
def no_mfa_requirement(monkeypatch):
    monkeypatch.setattr(settings, "platform_admin_requires_mfa", False)


async def owner(client, email="owner@platform.test"):
    account = await register_admin(client, email=email, org="Platform HQ")
    assert await _set_admin(email, True) == 0
    return account


async def test_the_back_office_is_invisible_to_customers(client):
    customer = await register_admin(client, email="c@shop.test", org="Shop")
    for path in ("/platform/overview", "/platform/health", "/platform/organizations", "/platform/audit"):
        res = await client.get(path, headers=customer.headers)
        assert res.status_code == 404, path
    assert (await client.get("/platform/overview")).status_code == 401
    me = (await client.get("/auth/me", headers=customer.headers)).json()
    assert me["is_platform_admin"] is False


async def test_platform_admin_is_granted_only_from_the_command_line(client, capsys):
    assert await _set_admin("nobody@platform.test", True) == 1
    admin = await owner(client)
    assert (await client.get("/auth/me", headers=admin.headers)).json()["is_platform_admin"] is True
    assert "can now open the back office" in capsys.readouterr().out


async def test_two_factor_is_required_when_configured(client, monkeypatch):
    admin = await owner(client)
    monkeypatch.setattr(settings, "platform_admin_requires_mfa", True)
    res = await client.get("/platform/overview", headers=admin.headers)
    assert res.status_code == 403 and res.json()["error"]["code"] == "mfa_required"


async def test_the_owner_sees_every_account(client):
    await register_admin(client, email="a@one.test", org="One Resale")
    await register_admin(client, email="b@two.test", org="Two Supply", account_type="supplier")
    admin = await owner(client)
    overview = (await client.get("/platform/overview", headers=admin.headers)).json()
    assert overview["organizations"] == 3 and overview["users"] == 3
    assert overview["organizations_by_type"]["supplier"] == 1
    found = (await client.get("/platform/organizations", params={"q": "b@two"}, headers=admin.headers)).json()
    assert [o["name"] for o in found] == ["Two Supply"]
    detail = (await client.get(f"/platform/organizations/{found[0]['id']}", headers=admin.headers)).json()
    assert detail["users"][0]["email"] == "b@two.test"


async def test_health_checks_report_plainly(client, capsys):
    admin = await owner(client)
    body = (await client.get("/platform/health", headers=admin.headers)).json()
    checks = {c["name"]: c for c in body["checks"]}
    assert checks["Database"]["status"] == "ok" and checks["File storage"]["status"] == "ok"
    assert checks["Database schema"]["detail"].startswith("Migration 00")
    assert checks["Payments (Stripe)"]["status"] == "warn"
    assert body["status"] in ("ok", "warn")
    assert await _health() == 0
    assert "OK    Database" in capsys.readouterr().out


async def test_credit_adjustments_are_ledgered_and_audited(client):
    shop = await register_admin(client, email="a@one.test", org="One Resale")
    admin = await owner(client)
    before = (await client.get(f"/platform/organizations/{shop.org_id}", headers=admin.headers)).json()["credit_balance"]
    res = await client.post(f"/platform/organizations/{shop.org_id}/credits",
                            json={"delta": 25, "note": "Refund for a failed analysis"}, headers=admin.headers)
    assert res.status_code == 200 and res.json()["balance"] == before + 25
    too_much = await client.post(f"/platform/organizations/{shop.org_id}/credits",
                                 json={"delta": -100000, "note": "oops"}, headers=admin.headers)
    assert too_much.status_code == 409
    assert (await client.post(f"/platform/organizations/{shop.org_id}/credits", json={"delta": 5},
                              headers=admin.headers)).status_code == 422  # a reason is required
    detail = (await client.get(f"/platform/organizations/{shop.org_id}", headers=admin.headers)).json()
    assert detail["credit_transactions"][0]["note"] == "Platform: Refund for a failed analysis"
    audit = (await client.get("/platform/audit", headers=admin.headers)).json()
    assert audit[0]["action"] == "credits.adjusted" and audit[0]["actor_email"] == "owner@platform.test"
    assert audit[0]["meta"]["note"] == "Refund for a failed analysis"


async def test_trial_extension_and_deactivation(client):
    shop = await register_admin(client, email="a@one.test", org="One Resale")
    admin = await owner(client)
    res = await client.post(f"/platform/organizations/{shop.org_id}/extend-trial",
                            json={"days": 7, "note": "Onboarding call ran late"}, headers=admin.headers)
    assert res.status_code == 200
    off = await client.post(f"/platform/users/{shop.user_id}/active", json={"active": False, "note": "Fraud report"},
                            headers=admin.headers)
    assert off.status_code == 200 and off.json()["is_active"] is False
    assert (await client.get("/auth/me", headers=shop.headers)).status_code == 401  # signed out everywhere
    self_off = await client.post(f"/platform/users/{admin.user_id}/active", json={"active": False, "note": "test"},
                                 headers=admin.headers)
    assert self_off.status_code == 409


async def test_invites_can_be_reviewed_resent_and_opt_outs_lifted(client, worker, fake_web, admin_engine):
    shopper = await buyer(client)
    await new_request(client, worker, shopper)
    admin = await owner(client)
    rows = (await client.get("/platform/invites", params={"q": "acmetools"}, headers=admin.headers)).json()
    assert len(rows) == 1 and rows[0]["email"] == "sales@acmetools.example" and rows[0]["status"] == "sent"
    invite_id = rows[0]["id"]
    mail.outbox.clear()
    cooled = await client.post(f"/platform/invites/{invite_id}/send", json={"note": "check"}, headers=admin.headers)
    assert cooled.json()["skipped"] == "cooldown" and invite_mail() == []
    forced = await client.post(f"/platform/invites/{invite_id}/send", json={"note": "Supplier asked", "force": True},
                               headers=admin.headers)
    assert forced.json()["status"] == "sent" and len(invite_mail()) == 1
    held = await client.post(f"/platform/invites/{invite_id}/hold", json={"note": "Wrong contact"}, headers=admin.headers)
    assert held.json()["status"] == "held"

    token = invite_mail()[0]["body"].split("unsubscribe?token=")[1].split()[0]
    await client.post("/invites/unsubscribe", json={"token": token})
    mail.outbox.clear()
    blocked = await client.post(f"/platform/invites/{invite_id}/send", json={"note": "retry", "force": True},
                                headers=admin.headers)
    assert blocked.json()["skipped"] == "suppressed" and invite_mail() == []  # force never overrides an opt-out
    values = {s["value"] for s in (await client.get("/platform/suppressions", headers=admin.headers)).json()}
    assert values == {"sales@acmetools.example", "acmetools.example"}
    lift = await client.post("/platform/suppressions/lift",
                             json={"value": "acmetools.example", "note": "Owner emailed us asking to rejoin"},
                             headers=admin.headers)
    assert lift.status_code == 200
    actions = [a["action"] for a in (await client.get("/platform/audit", headers=admin.headers)).json()]
    assert actions[:5] == ["suppression.lifted", "invite.sent", "invite.held", "invite.sent", "invite.sent"]


async def test_failed_jobs_can_be_retried_across_accounts(client, admin_engine):
    shop = await register_admin(client, email="a@one.test", org="One Resale")
    admin = await owner(client)
    async with admin_engine.begin() as conn:
        job_id = (await conn.execute(text(
            "INSERT INTO jobs (id, organization_id, queue, kind, payload, status, priority, attempts, max_attempts, "
            "run_at, last_error, created_at) VALUES (gen_random_uuid(), :org, 'default', 'test.kind', '{}', 'dead', 0, 5, 5, "
            "now(), 'boom', now()) RETURNING id"), {"org": shop.org_id})).scalar()
    dead = (await client.get("/platform/jobs", params={"status": "dead"}, headers=admin.headers)).json()
    assert [j["organization_name"] for j in dead] == ["One Resale"] and dead[0]["last_error"] == "boom"
    res = await client.post(f"/platform/jobs/{job_id}/retry", json={"note": "Provider outage over"}, headers=admin.headers)
    assert res.status_code == 200 and res.json()["status"] == "queued"
    again = await client.post(f"/platform/jobs/{job_id}/retry", json={"note": "again"}, headers=admin.headers)
    assert again.status_code == 409


async def test_customers_cannot_read_the_platform_audit(client, admin_engine):
    await owner(client)
    from app.db import SessionLocal, set_tenant

    shop = await register_admin(client, email="a@one.test", org="One Resale")
    async with SessionLocal() as db:
        await set_tenant(db, shop.org_id)
        assert (await db.execute(text("SELECT count(*) FROM platform_audit"))).scalar() == 0
    async with admin_engine.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM platform_audit"))).scalar() == 1
