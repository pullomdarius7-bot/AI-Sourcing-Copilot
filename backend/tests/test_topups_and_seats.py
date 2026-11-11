"""Pay-as-you-go credit top-ups, the plan/purchased credit pools, seat limits and the public price list."""
import json
import pathlib
import re

import pytest
from sqlalchemy import text

from app.ai.service import AIService, set_ai_service
from app.billing import plans
from tests.fakes import FakeProvider, good_analysis
from tests.helpers import add_member, register_admin
from tests.samples import QUOTE
from tests.test_billing import FakeStripe, deliver, invoice, subscribe


async def me(client, account):
    return (await client.get("/auth/me", headers=account.headers)).json()


async def billing(client, account):
    return (await client.get("/billing", headers=account.headers)).json()


async def sql(admin_engine, statement, **params):
    async with admin_engine.begin() as conn:
        return await conn.execute(text(statement), params)


async def set_credits(admin_engine, org_id, amount, purchased=0):
    await sql(admin_engine, "UPDATE organizations SET credit_balance = :a, purchased_credits = :p WHERE id = :o",
              a=amount, p=purchased, o=org_id)


@pytest.fixture
def stripe_fake():
    from app.billing.stripe_api import set_stripe

    fake = FakeStripe()

    async def create_topup_session(**kw):
        fake.topups.append(kw)
        return "https://checkout.stripe.test/topup"

    fake.topups = []
    fake.create_topup_session = create_topup_session
    set_stripe(fake)
    return fake


def topup_session(account, credits=20, unit=350, amount=None, status="paid", session="cs_topup_1"):
    return {
        "id": session, "mode": "payment", "client_reference_id": account.org_id, "customer": "cus_1",
        "payment_status": status, "amount_total": credits * unit if amount is None else amount, "currency": "usd",
        "metadata": {"organization_id": account.org_id, "kind": "credit_topup", "credits": str(credits),
                     "unit_amount": str(unit)},
    }


# --- Who can buy ----------------------------------------------------------------------------------------
async def test_trial_accounts_cannot_buy_credits(client, stripe_fake, admin_engine):
    admin = await register_admin(client)
    await set_credits(admin_engine, admin.org_id, 0)
    body = (await billing(client, admin))["topup"]
    assert body["available"] is False and "subscribers" in body["reason"]
    res = await client.post("/billing/create-topup-session", json={"credits": 20}, headers=admin.headers)
    assert res.status_code == 409 and res.json()["error"]["code"] == "topup_unavailable"
    assert stripe_fake.topups == []


async def test_subscribers_can_buy_once_they_are_out_of_credits(client, stripe_fake, admin_engine):
    admin = await register_admin(client)
    await subscribe(client, admin)  # 25 credits
    assert (await billing(client, admin))["topup"]["available"] is False
    res = await client.post("/billing/create-topup-session", json={"credits": 20}, headers=admin.headers)
    assert res.status_code == 409

    await set_credits(admin_engine, admin.org_id, 2)
    body = (await billing(client, admin))["topup"]
    assert (body["available"], body["price_cents"]) == (True, 350)
    res = await client.post("/billing/create-topup-session", json={"credits": 20}, headers=admin.headers)
    assert res.status_code == 200 and res.json()["url"] == "https://checkout.stripe.test/topup"
    session = stripe_fake.topups[0]
    assert (session["credits"], session["unit_amount_cents"], session["customer"]) == (20, 350, "cus_1")
    assert session["metadata"] == {"organization_id": admin.org_id, "user_id": admin.user_id, "kind": "credit_topup",
                                   "credits": "20", "unit_amount": "350"}


@pytest.mark.parametrize("credits", [0, 4, 501])
async def test_pack_size_limits(client, stripe_fake, admin_engine, credits):
    admin = await register_admin(client)
    await subscribe(client, admin)
    await set_credits(admin_engine, admin.org_id, 0)
    res = await client.post("/billing/create-topup-session", json={"credits": credits}, headers=admin.headers)
    assert res.status_code == 400 and res.json()["error"]["code"] == "invalid_quantity"


async def test_the_team_plan_pays_less_per_extra_credit(client, stripe_fake, admin_engine):
    admin = await register_admin(client)
    await subscribe(client, admin, plan="enterprise_buyer_monthly")
    await set_credits(admin_engine, admin.org_id, 0)
    await client.post("/billing/create-topup-session", json={"credits": 100}, headers=admin.headers)
    assert stripe_fake.topups[0]["unit_amount_cents"] == 125


async def test_out_of_credits_402_says_a_top_up_is_possible(client, worker, admin_engine):
    from tests import extraction_fixtures as fx
    from tests.req_helpers import ready_document

    admin = await register_admin(client)
    doc_id = await ready_document(client, worker, admin)
    set_ai_service(AIService([FakeProvider([fx.good_extraction()])]))
    await set_credits(admin_engine, admin.org_id, 0)
    res = await client.post(f"/documents/{doc_id}/requirements/extract", headers=admin.headers)
    assert res.json()["error"]["can_top_up"] is False  # still on the free trial
    await subscribe(client, admin)
    await set_credits(admin_engine, admin.org_id, 0)
    res = await client.post(f"/documents/{doc_id}/requirements/extract", headers=admin.headers)
    assert res.status_code == 402 and res.json()["error"]["can_top_up"] is True


# --- Granting -------------------------------------------------------------------------------------------
async def test_a_paid_top_up_grants_the_credits_once(client, admin_engine):
    admin = await register_admin(client)
    await subscribe(client, admin)
    await set_credits(admin_engine, admin.org_id, 1)
    assert await deliver(client, "checkout.session.completed", topup_session(admin), event_id="evt_t1") == "processed"
    body = await billing(client, admin)
    assert (body["credit_balance"], body["purchased_credits"]) == (21, 20)
    assert body["transactions"][0]["kind"] == "purchase" and body["transactions"][0]["delta"] == 20
    # A redelivery, and the same session announced by another event, add nothing.
    await deliver(client, "checkout.session.completed", topup_session(admin), event_id="evt_t1")
    await deliver(client, "checkout.session.async_payment_succeeded", topup_session(admin), event_id="evt_t2")
    assert (await me(client, admin))["credit_balance"] == 21


async def test_unpaid_or_mispriced_top_ups_grant_nothing(client, admin_engine):
    admin = await register_admin(client)
    await subscribe(client, admin)
    await set_credits(admin_engine, admin.org_id, 0)
    await deliver(client, "checkout.session.completed", topup_session(admin, status="unpaid", session="cs_a"))
    await deliver(client, "checkout.session.completed", topup_session(admin, amount=350, session="cs_b"))
    assert (await me(client, admin))["credit_balance"] == 0
    await deliver(client, "checkout.session.async_payment_succeeded", topup_session(admin, session="cs_a"))
    assert (await me(client, admin))["credit_balance"] == 20  # the delayed payment arrived


async def test_plan_credits_are_spent_first_and_bought_credits_roll_over(client, worker, admin_engine):
    admin = await register_admin(client)
    await subscribe(client, admin)
    await set_credits(admin_engine, admin.org_id, 2)
    await deliver(client, "checkout.session.completed", topup_session(admin, credits=10))  # 12 = 2 plan + 10 bought
    set_ai_service(AIService([FakeProvider([good_analysis()])]))
    for name in ("a.txt", "b.txt", "c.txt"):
        await client.post("/documents", files={"file": (name, (QUOTE + name).encode())}, headers=admin.headers)
    await worker.drain()
    body = await billing(client, admin)
    assert (body["credit_balance"], body["purchased_credits"]) == (9, 9)  # 2 plan credits went first, then 1 bought
    await deliver(client, "invoice.payment_succeeded", invoice(invoice_id="in_renewal"))
    body = await billing(client, admin)
    assert (body["credit_balance"], body["purchased_credits"]) == (34, 9)  # 25 new plan credits + 9 bought kept


async def test_cancellation_removes_plan_credits_but_keeps_bought_ones(client, admin_engine):
    admin = await register_admin(client)
    await subscribe(client, admin)
    await set_credits(admin_engine, admin.org_id, 1)
    await deliver(client, "checkout.session.completed", topup_session(admin, credits=10))
    await deliver(client, "customer.subscription.deleted", {"id": "sub_1", "customer": "cus_1", "status": "canceled"})
    body = await billing(client, admin)
    assert (body["credit_balance"], body["purchased_credits"], body["ai_access"]) == (10, 10, False)
    await subscribe(client, admin, invoice="in_back", subscription="sub_2")  # resubscribing makes them usable again
    body = await billing(client, admin)
    assert (body["credit_balance"], body["purchased_credits"], body["ai_access"]) == (35, 10, True)


async def test_bought_credits_can_never_exceed_the_balance(admin_engine, client):
    from sqlalchemy.exc import IntegrityError

    admin = await register_admin(client)
    with pytest.raises(IntegrityError):
        await set_credits(admin_engine, admin.org_id, 5, purchased=6)


# --- Seats -------------------------------------------------------------------------------------------------
async def test_the_team_plan_allows_five_seats_including_open_invitations(client):
    admin = await register_admin(client, account_type="solo_buyer", org=None)
    await subscribe(client, admin, plan="enterprise_buyer_monthly")
    for n in range(3):
        await add_member(client, admin, f"m{n}@acme.test", "buyer")
    res = await client.post("/invitations", json={"email": "m3@acme.test", "role": "buyer"}, headers=admin.headers)
    assert res.status_code == 201  # 4 people + 1 invitation = 5
    assert (await client.get("/seats", headers=admin.headers)).json() == {"used": 5, "limit": 5}
    res = await client.post("/invitations", json={"email": "m4@acme.test", "role": "buyer"}, headers=admin.headers)
    assert res.status_code == 409
    error = res.json()["error"]
    assert (error["code"], error["seats_used"], error["seat_limit"]) == ("seat_limit_reached", 5, 5)
    assert (await billing(client, admin))["seats"] == {"used": 5, "limit": 5}


async def test_freeing_a_seat_allows_the_next_invitation(client):
    admin = await register_admin(client, account_type="solo_buyer", org=None)
    await subscribe(client, admin, plan="enterprise_buyer_monthly")
    members = [await add_member(client, admin, f"m{n}@acme.test", "buyer") for n in range(4)]
    assert (await client.post("/invitations", json={"email": "x@acme.test", "role": "buyer"}, headers=admin.headers)).status_code == 409
    res = await client.patch(f"/users/{members[0].user_id}", json={"is_active": False}, headers=admin.headers)
    assert res.status_code == 200
    assert (await client.post("/invitations", json={"email": "x@acme.test", "role": "buyer"}, headers=admin.headers)).status_code == 201
    res = await client.patch(f"/users/{members[0].user_id}", json={"is_active": True}, headers=admin.headers)
    assert res.status_code == 409 and res.json()["error"]["code"] == "seat_limit_reached"  # reactivating takes a seat


async def test_an_invitation_cannot_be_accepted_after_a_downgrade_leaves_no_seat(client, admin_engine):
    from tests.helpers import PASSWORD, token_from_email

    admin = await register_admin(client)
    for n in range(3):
        await add_member(client, admin, f"m{n}@acme.test", "buyer")
    await client.post("/invitations", json={"email": "late@acme.test", "role": "buyer"}, headers=admin.headers)
    await subscribe(client, admin, plan="solo_buyer_monthly")  # 1 seat: the team is now over its limit
    res = await client.post("/auth/accept-invitation",
                            json={"token": token_from_email("late@acme.test"), "full_name": "Late", "password": PASSWORD})
    assert res.status_code == 409 and res.json()["error"]["code"] == "seat_limit_reached"


async def test_team_accounts_on_trial_get_five_seats_and_suppliers_are_unlimited(client):
    team = await register_admin(client)
    assert (await client.get("/seats", headers=team.headers)).json() == {"used": 1, "limit": 5}
    seller = await register_admin(client, email="s@liq.test", org="Liq", account_type="supplier")
    assert (await client.get("/seats", headers=seller.headers)).json() == {"used": 1, "limit": None}


# --- One price list ----------------------------------------------------------------------------------------
async def test_the_public_price_list_needs_no_sign_in(client):
    res = await client.get("/billing/plans")
    assert res.status_code == 200
    by_key = {p["key"]: p for p in res.json()}
    assert by_key["enterprise_buyer_monthly"] == {
        "key": "enterprise_buyer_monthly", "name": "Sourcing Team", "price_cents": 24900, "monthly_credits": 250,
        "seats": 5, "topup_price_cents": 125, "account_types": ["solo_buyer", "org_buyer"],
    }


def test_the_pricing_pages_fallback_matches_the_plan_catalog():
    """The landing page renders prices from GET /billing/plans and falls back to a copy when the API is down;
    that copy must never drift from the catalog."""
    source = (pathlib.Path(__file__).resolve().parents[2] / "frontend/src/lib/plans.ts").read_text()
    match = re.search(r"/\* plans-json \*/(.*?)/\* end-plans-json \*/", source, re.S)
    assert match, "frontend/src/lib/plans.ts must hold the fallback between plans-json markers"
    fallback = {p["key"]: p for p in json.loads(match.group(1))}
    catalog = {k: {"key": p.key, "name": p.name, "price_cents": p.price_cents, "monthly_credits": p.monthly_credits,
                   "seats": p.seats, "topup_price_cents": p.topup_price_cents,
                   "account_types": [t.value for t in p.account_types]} for k, p in plans.PLANS.items()}
    assert fallback == catalog


def test_the_landing_page_takes_numbers_from_the_catalog_not_from_hard_coded_copy():
    landing = (pathlib.Path(__file__).resolve().parents[2] / "frontend/src/components/landing/landing.tsx").read_text()
    for stale in ("Unlimited manifest", "$79", "$249", "$149", "25 Full Manifest", "Up to 5 team seats"):
        assert stale not in landing, stale
