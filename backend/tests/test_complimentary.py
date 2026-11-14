"""Complimentary accounts (the platform owner's own): AI without credits, plan, trial or seat limits."""
import pytest
from sqlalchemy import text

from app.config import get_settings
from app.ops import _set_admin, _set_comp
from tests.helpers import register_admin
from tests.req_helpers import extract, ready_document

settings = get_settings()


@pytest.fixture(autouse=True)
def _no_mfa(monkeypatch):
    monkeypatch.setattr(settings, "platform_admin_requires_mfa", False)


async def me(client, account):
    return (await client.get("/auth/me", headers=account.headers)).json()


async def test_a_complimentary_account_uses_ai_without_spending_or_a_plan(client, worker, admin_engine):
    owner = await register_admin(client, email="owner@platform.test", org="Platform HQ")
    assert await _set_comp("owner@platform.test", True) == 0
    async with admin_engine.begin() as conn:  # no credits and an expired trial: a normal account would be blocked
        await conn.execute(text("UPDATE organizations SET credit_balance = 0, trial_ends_at = now() - interval '1 day'"))
    profile = await me(client, owner)
    assert profile["billing_exempt"] is True and profile["ai_access"] is True

    doc_id = await ready_document(client, worker, owner)
    run_id, _ = await extract(client, worker, owner, doc_id)
    run = (await client.get(f"/extraction-runs/{run_id}", headers=owner.headers)).json()
    assert run["status"] != "failed"
    assert (await me(client, owner))["credit_balance"] == 0  # nothing was charged
    async with admin_engine.connect() as conn:
        charges = (await conn.execute(text("SELECT count(*) FROM credit_transactions WHERE kind = 'usage'"))).scalar()
    assert charges == 0

    billing = (await client.get("/billing", headers=owner.headers)).json()
    assert billing["billing_exempt"] is True and billing["seats"]["limit"] is None and billing["team_features"] is True


async def test_normal_accounts_still_pay(client, worker, admin_engine):
    shop = await register_admin(client, email="a@one.test", org="One Resale")
    async with admin_engine.begin() as conn:
        await conn.execute(text("UPDATE organizations SET trial_ends_at = now() - interval '1 day'"))
    profile = await me(client, shop)
    assert profile["billing_exempt"] is False and profile["ai_access"] is False


async def test_only_the_platform_can_make_an_account_complimentary(client, admin_engine):
    shop = await register_admin(client, email="a@one.test", org="One Resale")
    owner = await register_admin(client, email="owner@platform.test", org="Platform HQ")
    assert await _set_admin("owner@platform.test", True) == 0
    assert (await client.post(f"/platform/organizations/{shop.org_id}/complimentary",
                              json={"exempt": True, "note": "x"}, headers=shop.headers)).status_code == 404
    res = await client.post(f"/platform/organizations/{shop.org_id}/complimentary",
                            json={"exempt": True, "note": "Founding partner"}, headers=owner.headers)
    assert res.status_code == 200 and (await me(client, shop))["billing_exempt"] is True
    detail = (await client.get(f"/platform/organizations/{shop.org_id}", headers=owner.headers)).json()
    assert detail["billing_exempt"] is True
    audit = (await client.get("/platform/audit", headers=owner.headers)).json()
    assert audit[0]["action"] == "account.comped" and audit[0]["meta"]["note"] == "Founding partner"
    assert await _set_comp("nobody@x.test", True) == 1
