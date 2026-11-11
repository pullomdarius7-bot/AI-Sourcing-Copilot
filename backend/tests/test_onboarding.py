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


@pytest.mark.parametrize("account_type", ["org_buyer", "supplier"])
async def test_team_and_supplier_accounts_need_an_organization_name(client, account_type):
    res = await client.post(
        "/auth/register",
        json={"account_type": account_type, "full_name": "Ada", "email": "a@acme.test", "password": PASSWORD},
    )
    assert res.status_code == 422, res.text


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
    assert {f["key"] for f in form["fields"]} == set(ONBOARDING_ANSWERS[account_type])

    await complete_onboarding(client, account, account_type)
    after = (await client.get("/onboarding", headers=account.headers)).json()
    assert after["completed"] is True
    assert after["profile"]["business_type"] == ONBOARDING_ANSWERS[account_type]["business_type"]
    assert after["profile"]["product_categories"] == sorted(ONBOARDING_ANSWERS[account_type]["product_categories"])
    assert (await client.get("/auth/me", headers=account.headers)).json()["onboarding_completed"] is True


def test_supplier_is_asked_for_volume_and_buyers_for_budget():
    assert "monthly_inventory_volume" in ONBOARDING_ANSWERS["supplier"]
    assert "monthly_sourcing_budget" not in ONBOARDING_ANSWERS["supplier"]
    assert "monthly_sourcing_budget" in ONBOARDING_ANSWERS["solo_buyer"]
    assert "team_size" in ONBOARDING_ANSWERS["org_buyer"] and "team_size" not in ONBOARDING_ANSWERS["solo_buyer"]


@pytest.mark.parametrize(
    "change",
    [
        {"business_type": "astronaut"},  # not an option
        {"product_categories": []},  # nothing chosen
        {"product_categories": ["apparel", "weapons"]},  # one unknown option
        {"product_categories": "apparel"},  # wrong shape
        {"monthly_sourcing_budget": None},  # missing
        {"monthly_inventory_volume": "1_5_truckloads"},  # a supplier question on a buyer form
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
