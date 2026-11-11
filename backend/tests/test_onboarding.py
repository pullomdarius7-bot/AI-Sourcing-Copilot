import pytest
from sqlalchemy import text

from tests.helpers import (
    ONBOARDING_ANSWERS,
    PASSWORD,
    add_member,
    complete_onboarding,
    register_admin,
)

ACCOUNT_TYPES = ["solo_buyer", "org_buyer", "supplier"]


# --- Registration ------------------------------------------------------------------------------------
@pytest.mark.parametrize("account_type", ACCOUNT_TYPES)
async def test_anyone_can_register_each_account_type_and_starts_unqualified(client, account_type):
    account = await register_admin(client, account_type=account_type, onboard=False)
    me = (await client.get("/auth/me", headers=account.headers)).json()
    assert me["account_type"] == account_type
    assert me["onboarding_completed"] is False
    assert me["role"] == "admin"


async def test_solo_buyer_needs_no_organization_name(client):
    account = await register_admin(client, account_type="solo_buyer", org=None, onboard=False)
    me = (await client.get("/auth/me", headers=account.headers)).json()
    assert me["organization_name"] == "Ada Admin"


async def test_signup_without_a_role_defaults_to_solo_buyer_until_the_wizard(client):
    res = await client.post(
        "/auth/register", json={"full_name": "Ada", "email": "a@acme.test", "password": PASSWORD}
    )
    assert res.status_code == 201, res.text


async def test_unknown_account_type_is_rejected(client):
    res = await client.post(
        "/auth/register",
        json={"account_type": "admin", "organization_name": "Acme", "full_name": "Ada", "email": "a@acme.test",
              "password": PASSWORD},
    )
    assert res.status_code == 422, res.text


# --- The gate ----------------------------------------------------------------------------------------
@pytest.mark.parametrize("path", ["/documents", "/suppliers", "/rfqs", "/users", "/organization", "/jobs/stats"])
async def test_features_are_blocked_until_onboarding_is_complete(client, path):
    account = await register_admin(client, onboard=False)
    res = await client.get(path, headers=account.headers)
    assert res.status_code == 403, res.text
    assert res.json()["error"]["code"] == "onboarding_required"

    await complete_onboarding(client, account)
    assert (await client.get(path, headers=account.headers)).status_code == 200


async def test_account_and_security_endpoints_work_before_onboarding(client):
    account = await register_admin(client, onboard=False)
    assert (await client.get("/auth/sessions", headers=account.headers)).status_code == 200
    assert (await client.post("/auth/logout", headers=account.headers)).status_code == 200


# --- The form ----------------------------------------------------------------------------------------
@pytest.mark.parametrize("account_type", ACCOUNT_TYPES)
async def test_form_matches_account_type_and_answers_are_stored(client, account_type):
    account = await register_admin(client, account_type=account_type, onboard=False)
    form = (await client.get("/onboarding", headers=account.headers)).json()
    assert form["account_type"] == account_type
    assert form["completed"] is False and form["can_edit"] is True
    assert [r["value"] for r in form["roles"]] == ACCOUNT_TYPES
    assert {f["key"] for f in form["forms"][account_type]} == set(ONBOARDING_ANSWERS[account_type])

    await complete_onboarding(client, account, account_type)
    after = (await client.get("/onboarding", headers=account.headers)).json()
    assert after["completed"] is True
    assert after["profile"] == {
        k: sorted(v) if isinstance(v, list) else v for k, v in ONBOARDING_ANSWERS[account_type].items()
    }
    assert (await client.get("/auth/me", headers=account.headers)).json()["onboarding_completed"] is True


@pytest.mark.parametrize(
    "change",
    [
        {"monthly_budget": "a_lot"},  # not an option
        {"product_categories": []},  # nothing chosen
        {"product_categories": ["footwear", "weapons"]},  # one unknown option
        {"product_categories": "footwear"},  # wrong shape
        {"lot_sizes": None},  # missing
        {"dispatch_regions": ["midwest"]},  # a supplier question on a buyer form
        {"is_admin": "true"},  # arbitrary key
    ],
)
async def test_invalid_answers_are_rejected_and_nothing_is_stored(client, change):
    account = await register_admin(client, account_type="solo_buyer", onboard=False)
    answers = {**ONBOARDING_ANSWERS["solo_buyer"], **change}
    answers = {k: v for k, v in answers.items() if v is not None}
    res = await client.put("/onboarding", json={"answers": answers}, headers=account.headers)
    assert res.status_code == 422, res.text
    state = (await client.get("/onboarding", headers=account.headers)).json()
    assert state["completed"] is False and state["profile"] == {}


async def test_only_an_administrator_can_change_the_profile(client):
    admin = await register_admin(client)
    buyer = await add_member(client, admin, "buyer@acme.test", "buyer")
    form = (await client.get("/onboarding", headers=buyer.headers)).json()
    assert form["can_edit"] is False
    res = await client.put("/onboarding", json={"answers": ONBOARDING_ANSWERS["org_buyer"]}, headers=buyer.headers)
    assert res.status_code == 403, res.text


async def test_onboarding_is_audited(client):
    admin = await register_admin(client)
    actions = [e["action"] for e in (await client.get("/audit-logs", headers=admin.headers)).json()]
    assert "onboarding.completed" in actions


# --- Role selection (wizard step 1) --------------------------------------------------------------------
async def test_role_chosen_in_the_wizard_replaces_the_signup_hint(client):
    account = await register_admin(client, account_type="solo_buyer", org=None, onboard=False)
    res = await client.put(
        "/onboarding",
        json={"account_type": "supplier", "organization_name": "Pallet Kings", "answers": ONBOARDING_ANSWERS["supplier"]},
        headers=account.headers,
    )
    assert res.status_code == 200, res.text
    me = (await client.get("/auth/me", headers=account.headers)).json()
    assert (me["account_type"], me["organization_name"]) == ("supplier", "Pallet Kings")


async def test_answers_must_match_the_role_chosen_in_the_wizard(client):
    account = await register_admin(client, account_type="solo_buyer", onboard=False)
    res = await client.put(
        "/onboarding", json={"account_type": "supplier", "answers": ONBOARDING_ANSWERS["solo_buyer"]},
        headers=account.headers,
    )
    assert res.status_code == 422, res.text
    assert (await client.get("/auth/me", headers=account.headers)).json()["account_type"] == "solo_buyer"


async def test_account_type_is_locked_once_onboarding_is_complete(client):
    account = await register_admin(client, account_type="solo_buyer")
    res = await client.put(
        "/onboarding", json={"account_type": "org_buyer", "answers": ONBOARDING_ANSWERS["org_buyer"]},
        headers=account.headers,
    )
    assert res.status_code == 409, res.text
    assert res.json()["error"]["code"] == "account_type_locked"


# --- Trial provisioning (wizard step 3) ----------------------------------------------------------------
@pytest.mark.parametrize("account_type", ACCOUNT_TYPES)
async def test_onboarding_starts_the_trial_with_ten_analyses_for_seven_days(client, admin_engine, account_type):
    from datetime import UTC, datetime, timedelta

    account = await register_admin(client, account_type=account_type, onboard=False)
    before = (await client.get("/auth/me", headers=account.headers)).json()
    assert (before["credit_balance"], before["trial_ends_at"]) == (0, None)

    await complete_onboarding(client, account, account_type)
    me = (await client.get("/auth/me", headers=account.headers)).json()
    assert me["credit_balance"] == 10
    ends = datetime.fromisoformat(me["trial_ends_at"])
    assert timedelta(days=6, hours=23) < ends - datetime.now(UTC) <= timedelta(days=7)
    async with admin_engine.connect() as conn:
        rows = (await conn.execute(text("SELECT kind, delta, balance_after, reference FROM credit_transactions"))).all()
    assert [tuple(r) for r in rows] == [("trial_grant", 10, 10, "trial")]


async def test_resubmitting_the_profile_never_grants_a_second_trial(client, admin_engine):
    account = await register_admin(client)
    first = (await client.get("/auth/me", headers=account.headers)).json()
    await complete_onboarding(client, account)
    await complete_onboarding(client, account)
    me = (await client.get("/auth/me", headers=account.headers)).json()
    assert (me["credit_balance"], me["trial_ends_at"]) == (10, first["trial_ends_at"])
    async with admin_engine.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM credit_transactions")) == 1


async def test_simultaneous_submissions_grant_one_trial(client, admin_engine):
    import asyncio

    account = await register_admin(client, onboard=False)
    await asyncio.gather(*(complete_onboarding(client, account) for _ in range(5)))
    async with admin_engine.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM credit_transactions")) == 1
        assert await conn.scalar(text("SELECT credit_balance FROM organizations")) == 10


# --- Credit ledger -------------------------------------------------------------------------------------
async def _post(org_id, delta, kind, **kw):
    from app.billing import ledger
    from app.db import SessionLocal, set_tenant

    async with SessionLocal() as db:
        await set_tenant(db, org_id)
        entry = await ledger.post(db, org_id, delta, kind, **kw)
        await db.commit()
        return entry.balance_after


async def test_ledger_spends_down_to_zero_and_then_refuses(client):
    from app.billing.ledger import InsufficientCredits

    account = await register_admin(client)
    for expected in range(9, -1, -1):
        assert await _post(account.org_id, -1, "usage") == expected
    with pytest.raises(InsufficientCredits):
        await _post(account.org_id, -1, "usage")
    assert (await client.get("/auth/me", headers=account.headers)).json()["credit_balance"] == 0


async def test_ledger_charge_with_the_same_reference_is_applied_once(client):
    account = await register_admin(client)
    assert await _post(account.org_id, -1, "usage", reference="job:42") == 9
    assert await _post(account.org_id, -1, "usage", reference="job:42") == 9
    assert (await client.get("/auth/me", headers=account.headers)).json()["credit_balance"] == 9


async def test_concurrent_charges_cannot_overspend(client):
    import asyncio

    from app.billing.ledger import InsufficientCredits

    account = await register_admin(client)
    results = await asyncio.gather(*(_post(account.org_id, -1, "usage") for _ in range(15)), return_exceptions=True)
    assert sum(1 for r in results if isinstance(r, int)) == 10
    assert sum(1 for r in results if isinstance(r, InsufficientCredits)) == 5
    assert (await client.get("/auth/me", headers=account.headers)).json()["credit_balance"] == 0


async def test_ledger_is_tenant_isolated_and_append_only(client):
    from sqlalchemy.exc import ProgrammingError

    from app.db import SessionLocal, set_tenant

    a = await register_admin(client, email="a@acme.test", org="Acme")
    b = await register_admin(client, email="b@globex.test", org="Globex")
    async with SessionLocal() as db:
        await set_tenant(db, a.org_id)
        orgs = (await db.execute(text("SELECT DISTINCT organization_id::text FROM credit_transactions"))).scalars().all()
        assert orgs == [a.org_id]
        with pytest.raises(ProgrammingError):  # WITH CHECK: cannot write into another tenant
            await db.execute(text(
                "INSERT INTO credit_transactions (organization_id, kind, delta, balance_after) "
                f"VALUES ('{b.org_id}', 'adjustment', 1000, 1000)"
            ))
    for statement in ("UPDATE credit_transactions SET delta = 1000", "DELETE FROM credit_transactions"):
        async with SessionLocal() as db:
            await set_tenant(db, a.org_id)
            with pytest.raises(ProgrammingError):
                await db.execute(text(statement))


# --- Account type rules ------------------------------------------------------------------------------
async def test_solo_buyer_cannot_invite_members(client):
    solo = await register_admin(client, account_type="solo_buyer")
    res = await client.post("/invitations", json={"email": "x@acme.test", "role": "buyer"}, headers=solo.headers)
    assert res.status_code == 409, res.text
    assert res.json()["error"]["code"] == "solo_account"


async def test_org_buyer_can_build_a_team(client):
    admin = await register_admin(client, account_type="org_buyer")
    member = await add_member(client, admin, "buyer@acme.test", "buyer")
    me = (await client.get("/auth/me", headers=member.headers)).json()
    assert me["account_type"] == "org_buyer" and me["onboarding_completed"] is True  # members skip the form


# --- Isolation ---------------------------------------------------------------------------------------
async def test_one_accounts_onboarding_never_affects_another(client):
    done = await register_admin(client, email="a@acme.test", org="Acme")
    pending = await register_admin(client, email="b@globex.test", org="Globex", account_type="supplier", onboard=False)
    assert (await client.get("/onboarding", headers=pending.headers)).json()["profile"] == {}
    assert (await client.get("/documents", headers=pending.headers)).status_code == 403
    assert (await client.get("/documents", headers=done.headers)).status_code == 200


async def test_rls_hides_other_accounts_profiles(client):
    from app.db import SessionLocal, set_tenant

    a = await register_admin(client, email="a@acme.test", org="Acme")
    await register_admin(client, email="b@globex.test", org="Globex", account_type="supplier")
    async with SessionLocal() as db:
        await set_tenant(db, a.org_id)
        rows = (await db.execute(text("SELECT id::text, account_type FROM organizations"))).all()
    assert [(r[0], r[1]) for r in rows] == [(a.org_id, "org_buyer")]


# --- Migration ---------------------------------------------------------------------------------------
async def test_database_rejects_unknown_account_type(admin_engine):
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        async with admin_engine.begin() as conn:
            await conn.execute(text("INSERT INTO organizations (name, slug, account_type) VALUES ('x', 'x', 'hacker')"))


async def test_new_organizations_default_to_not_onboarded(admin_engine):
    async with admin_engine.begin() as conn:
        row = (await conn.execute(text(
            "INSERT INTO organizations (name, slug) VALUES ('x', 'x-default') "
            "RETURNING account_type, onboarding_completed, onboarding_profile"
        ))).one()
    assert (row[0], row[1], row[2]) == ("org_buyer", False, {})


async def test_database_refuses_a_negative_balance(admin_engine):
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        async with admin_engine.begin() as conn:
            await conn.execute(text("INSERT INTO organizations (name, slug, credit_balance) VALUES ('x', 'x-neg', -1)"))
