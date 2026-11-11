"""Stripe billing, credit metering and the AI gateway."""
import asyncio
import hashlib
import hmac
import json
import pathlib
import re
import time
import uuid

import pytest
from sqlalchemy import text

from app import email as mail
from app.ai import gateway
from app.ai.providers import ProviderError
from app.ai.service import AIService, set_ai_service
from app.billing import plans
from app.billing.stripe_api import set_stripe
from app.db import SessionLocal, set_tenant
from tests import extraction_fixtures as fx
from tests.fakes import FakeProvider, good_analysis, transient
from tests.helpers import add_member, register_admin
from tests.req_helpers import ready_document
from tests.samples import QUOTE

WEBHOOK_SECRET = "whsec_test_secret"


# --- Helpers -----------------------------------------------------------------------------------------
async def me(client, account):
    return (await client.get("/auth/me", headers=account.headers)).json()


async def balance(client, account) -> int:
    return (await me(client, account))["credit_balance"]


async def sql(admin_engine, statement, **params):
    async with admin_engine.begin() as conn:
        return await conn.execute(text(statement), params)


async def transactions(admin_engine, org_id):
    rows = await sql(
        admin_engine,
        "SELECT kind, action, delta, balance_after, state, subject_id::text FROM credit_transactions "
        "WHERE organization_id = :o ORDER BY id", o=org_id,
    )
    return [tuple(r) for r in rows]


async def expire_trial(admin_engine, org_id):
    await sql(admin_engine, "UPDATE organizations SET trial_ends_at = now() - interval '1 minute' WHERE id = :o", o=org_id)


async def set_credits(admin_engine, org_id, amount):
    await sql(admin_engine, "UPDATE organizations SET credit_balance = :a WHERE id = :o", a=amount, o=org_id)


async def upload(client, worker, account, name="quote.txt", data=None):
    res = await client.post("/documents", files={"file": (name, data or QUOTE.encode())}, headers=account.headers)
    assert res.status_code == 201, res.text
    await worker.drain()
    doc_id = res.json()["document"]["id"]
    return doc_id, (await client.get(f"/documents/{doc_id}", headers=account.headers)).json()


def signed(event: dict, secret: str = WEBHOOK_SECRET, timestamp: int | None = None) -> tuple[bytes, dict]:
    payload = json.dumps(event).encode()
    ts = timestamp or int(time.time())
    digest = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return payload, {"stripe-signature": f"t={ts},v1={digest}", "content-type": "application/json"}


async def deliver(client, event_type: str, obj: dict, event_id: str | None = None):
    event = {"id": event_id or f"evt_{uuid.uuid4().hex}", "type": event_type, "data": {"object": obj}}
    payload, headers = signed(event)
    res = await client.post("/webhooks/stripe", content=payload, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["result"]


class FakeStripe:
    def __init__(self):
        self.customers: list[dict] = []
        self.checkouts: list[dict] = []
        self.portals: list[dict] = []

    async def create_customer(self, **kw):
        self.customers.append(kw)
        return f"cus_{len(self.customers)}"

    async def create_checkout_session(self, **kw):
        self.checkouts.append(kw)
        return "https://checkout.stripe.test/session"

    async def create_portal_session(self, **kw):
        self.portals.append(kw)
        return "https://billing.stripe.test/portal"


@pytest.fixture
def stripe_fake():
    fake = FakeStripe()
    set_stripe(fake)
    return fake


async def subscribe(client, account, plan="solo_buyer_monthly", invoice="in_1", customer="cus_1", subscription="sub_1"):
    """What Stripe sends when a checkout is paid immediately."""
    return await deliver(client, "checkout.session.completed", {
        "id": "cs_1", "mode": "subscription", "client_reference_id": account.org_id, "customer": customer,
        "subscription": subscription, "payment_status": "paid", "invoice": invoice,
        "metadata": {"organization_id": account.org_id, "plan": plan},
    })


def invoice(customer="cus_1", invoice_id="in_2", amount=7900, plan="solo_buyer_monthly", period_end=1_900_000_000):
    return {
        "id": invoice_id, "customer": customer, "subscription": "sub_1", "amount_paid": amount,
        "billing_reason": "subscription_cycle",
        "lines": {"data": [{"price": {"id": "price_x", "lookup_key": plan}, "period": {"end": period_end}}]},
    }


# --- Pricing -----------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("items", "credits"), [(0, 1), (1, 1), (120, 1), (499, 1), (500, 5), (501, 6), (1000, 10), (2550, 26)]
)
def test_manifest_extraction_costs_one_credit_under_500_items_then_one_per_100(items, credits):
    assert plans.cost_of(plans.MANIFEST_EXTRACTION, items) == credits


def test_a_full_analysis_run_costs_one_credit():
    assert plans.cost_of(plans.DEEP_ANALYSIS) == 1


def test_plans_match_the_price_list():
    assert {k: (p.price_cents, p.monthly_credits) for k, p in plans.PLANS.items()} == {
        "solo_buyer_monthly": (7900, 25), "enterprise_buyer_monthly": (24900, 250), "supplier_monthly": (14900, 100),
    }


# --- Gateway: every model call is paid for first --------------------------------------------------------
def test_no_code_outside_the_gateway_calls_a_provider():
    app_dir = pathlib.Path(__file__).resolve().parent.parent / "app"
    offenders = [
        str(path.relative_to(app_dir)) for path in app_dir.rglob("*.py")
        if path.name != "gateway.py" and re.search(r"\.generate\(", path.read_text())
    ]
    assert offenders == []


async def test_successful_analysis_is_charged_once_with_its_document_as_reference(client, worker, admin_engine):
    admin = await register_admin(client)
    set_ai_service(AIService([FakeProvider([good_analysis()])]))
    doc_id, doc = await upload(client, worker, admin)
    assert doc["analysis_status"] == "succeeded"
    assert await balance(client, admin) == 9
    assert (await transactions(admin_engine, admin.org_id))[-1] == ("usage", "manifest_extraction", -1, 9, "settled", doc_id)


async def test_trial_expiry_blocks_ai_before_any_provider_is_contacted(client, worker, admin_engine):
    admin = await register_admin(client)
    await expire_trial(admin_engine, admin.org_id)
    provider = FakeProvider([good_analysis()])
    set_ai_service(AIService([provider]))
    _, doc = await upload(client, worker, admin)
    assert doc["analysis_status"] == "skipped" and doc["status"] == "processed"
    assert provider.requests == []
    assert await balance(client, admin) == 10  # nothing was charged
    assert (await me(client, admin))["ai_access"] is False


async def test_credit_exhaustion_blocks_ai_before_any_provider_is_contacted(client, worker, admin_engine):
    admin = await register_admin(client)
    await set_credits(admin_engine, admin.org_id, 0)
    provider = FakeProvider([good_analysis()])
    set_ai_service(AIService([provider]))
    _, doc = await upload(client, worker, admin)
    assert doc["analysis_status"] == "skipped"
    assert provider.requests == []


async def test_large_manifest_is_refused_when_the_balance_cannot_cover_it(client, worker, admin_engine):
    admin = await register_admin(client)
    await set_credits(admin_engine, admin.org_id, 5)
    provider = FakeProvider([good_analysis()])
    set_ai_service(AIService([provider]))
    rows = "sku,title,qty\n" + "".join(f"SKU{i},Item {i},1\n" for i in range(650))  # 651 lines -> 7 credits
    _, doc = await upload(client, worker, admin, name="manifest.csv", data=rows.encode())
    assert doc["analysis_status"] == "skipped" and provider.requests == []
    assert await balance(client, admin) == 5

    await set_credits(admin_engine, admin.org_id, 10)
    doc_id, doc = await upload(client, worker, admin, name="manifest2.csv", data=(rows + "X,Y,1\n").encode())
    assert len(provider.requests) == 1
    assert await balance(client, admin) == 3
    assert (await transactions(admin_engine, admin.org_id))[-1][:3] == ("usage", "manifest_extraction", -7)


async def test_failed_analysis_is_refunded(client, worker, admin_engine):
    admin = await register_admin(client)
    set_ai_service(AIService([FakeProvider([ProviderError("bad request", retryable=False)])]))
    _, doc = await upload(client, worker, admin)
    assert doc["analysis_status"] == "failed"
    assert await balance(client, admin) == 10
    kinds = [(t[0], t[2], t[4]) for t in await transactions(admin_engine, admin.org_id)]
    assert kinds == [("trial_grant", 10, "settled"), ("usage", -1, "refunded"), ("refund", 1, "settled")]


async def test_retries_after_transient_failures_charge_exactly_once(client, worker):
    admin = await register_admin(client)
    set_ai_service(AIService([FakeProvider([transient(), transient(), good_analysis()])]))
    _, doc = await upload(client, worker, admin)
    assert doc["analysis_status"] == "succeeded"
    assert await balance(client, admin) == 9


async def test_a_charge_left_held_by_a_crashed_worker_is_reused_not_repeated(client, admin_engine):
    admin = await register_admin(client)
    subject = uuid.uuid4()
    async with SessionLocal() as db:  # the worker charged, then died before storing a result
        await set_tenant(db, admin.org_id)
        await gateway.acquire(db, admin.org_id, plans.DEEP_ANALYSIS, subject)
    async with SessionLocal() as db:  # the retry
        await set_tenant(db, admin.org_id)
        permit = await gateway.acquire(db, admin.org_id, plans.DEEP_ANALYSIS, subject)
        await gateway.settle(db, permit)
        await db.commit()
    assert await balance(client, admin) == 9
    assert [t[4] for t in await transactions(admin_engine, admin.org_id) if t[0] == "usage"] == ["settled"]


async def test_concurrent_requests_cannot_spend_the_same_credit(client, admin_engine):
    admin = await register_admin(client)
    await set_credits(admin_engine, admin.org_id, 3)

    async def attempt():
        async with SessionLocal() as db:
            await set_tenant(db, admin.org_id)
            return await gateway.acquire(db, admin.org_id, plans.DEEP_ANALYSIS, uuid.uuid4())

    results = await asyncio.gather(*(attempt() for _ in range(12)), return_exceptions=True)
    assert sum(isinstance(r, gateway.Permit) for r in results) == 3
    assert sum(isinstance(r, gateway.PaymentRequired) for r in results) == 9
    assert await balance(client, admin) == 0


async def test_release_is_idempotent(client):
    admin = await register_admin(client)
    async with SessionLocal() as db:
        await set_tenant(db, admin.org_id)
        permit = await gateway.acquire(db, admin.org_id, plans.DEEP_ANALYSIS, uuid.uuid4())
        await gateway.release(db, permit)
        await gateway.release(db, permit)
    assert await balance(client, admin) == 10


async def test_a_closed_permit_cannot_dispatch(client):
    admin = await register_admin(client)
    provider = FakeProvider([good_analysis()])
    async with SessionLocal() as db:
        await set_tenant(db, admin.org_id)
        permit = await gateway.acquire(db, admin.org_id, plans.DEEP_ANALYSIS, uuid.uuid4())
        await gateway.release(db, permit)
        with pytest.raises(RuntimeError):
            await gateway.dispatch(permit, provider, None)
    assert provider.requests == []


# --- HTTP 402 ------------------------------------------------------------------------------------------
async def test_starting_an_analysis_without_credits_returns_402_with_the_upgrade_payload(client, worker, admin_engine):
    admin = await register_admin(client)
    doc_id = await ready_document(client, worker, admin)
    set_ai_service(AIService([FakeProvider([fx.good_extraction()])]))
    await set_credits(admin_engine, admin.org_id, 0)
    res = await client.post(f"/documents/{doc_id}/requirements/extract", headers=admin.headers)
    assert res.status_code == 402, res.text
    error = res.json()["error"]
    assert (error["code"], error["remaining"], error["upgrade_url"]) == ("INSUFFICIENT_CREDITS", 0, "/billing")
    assert (await client.get("/extraction-runs", headers=admin.headers)).json() == []  # nothing was queued


async def test_starting_an_analysis_after_the_trial_returns_402(client, worker, admin_engine):
    admin = await register_admin(client)
    doc_id = await ready_document(client, worker, admin)
    set_ai_service(AIService([FakeProvider([fx.good_extraction()])]))
    await expire_trial(admin_engine, admin.org_id)
    res = await client.post(f"/documents/{doc_id}/requirements/extract", headers=admin.headers)
    assert res.status_code == 402, res.text
    error = res.json()["error"]
    assert (error["code"], error["reason"], error["remaining"]) == ("SUBSCRIPTION_REQUIRED", "trial_expired", 10)


async def test_a_full_analysis_run_is_charged_one_credit_however_many_model_calls_it_makes(client, worker, admin_engine):
    admin = await register_admin(client)
    doc_id = await ready_document(client, worker, admin)
    provider = FakeProvider([{"nonsense": True}, fx.good_extraction()])  # first answer rejected, second accepted
    set_ai_service(AIService([provider]))
    res = await client.post(f"/documents/{doc_id}/requirements/extract", headers=admin.headers)
    assert res.status_code == 202, res.text
    await worker.drain()
    assert len(provider.requests) == 2
    assert await balance(client, admin) == 9
    assert (await transactions(admin_engine, admin.org_id))[-1] == ("usage", "deep_analysis", -1, 9, "settled", res.json()["id"])


async def test_assistant_answers_without_a_model_when_credits_run_out(client, admin_engine):
    admin = await register_admin(client)
    provider = FakeProvider([{"tools": [{"name": "lookup_term", "args": {"term": "moq"}}]}])
    set_ai_service(AIService([provider]))
    await set_credits(admin_engine, admin.org_id, 0)
    res = await client.post("/agent/ask", json={"question": "What is MOQ?"}, headers=admin.headers)
    assert res.status_code == 201, res.text
    assert res.json()["assistant"]["run"]["mode"] == "deterministic"
    assert "model_not_used_no_credits" in res.json()["assistant"]["run"]["flags"]
    assert provider.requests == []


async def test_assistant_model_answer_costs_one_credit(client, admin_engine):
    admin = await register_admin(client)
    provider = FakeProvider([{"tools": [{"name": "lookup_term", "args": {"term": "moq"}}]}])
    set_ai_service(AIService([provider]))
    res = await client.post("/agent/ask", json={"question": "What is MOQ?"}, headers=admin.headers)
    assert res.status_code == 201, res.text
    assert provider.requests
    assert await balance(client, admin) == 9
    assert (await transactions(admin_engine, admin.org_id))[-1][:5] == ("usage", "assistant_answer", -1, 9, "settled")


async def test_assistant_charge_is_refunded_when_every_provider_fails(client):
    admin = await register_admin(client)
    set_ai_service(AIService([FakeProvider([transient()])]))
    res = await client.post("/agent/ask", json={"question": "What is MOQ?"}, headers=admin.headers)
    assert res.status_code == 201, res.text
    assert await balance(client, admin) == 10


async def test_billing_can_be_switched_off_for_self_hosted_installs(client, worker, admin_engine, monkeypatch):
    admin = await register_admin(client)
    await set_credits(admin_engine, admin.org_id, 0)
    monkeypatch.setattr(gateway.settings, "billing_enforced", False)
    set_ai_service(AIService([FakeProvider([good_analysis()])]))
    _, doc = await upload(client, worker, admin)
    assert doc["analysis_status"] == "succeeded"


# --- Checkout and portal -------------------------------------------------------------------------------
async def test_checkout_creates_one_customer_mapped_to_the_account_and_carries_the_trial(client, stripe_fake, admin_engine):
    admin = await register_admin(client, account_type="solo_buyer", org=None)
    res = await client.post("/billing/create-checkout-session", json={"plan": "solo_buyer_monthly"}, headers=admin.headers)
    assert res.status_code == 200, res.text
    assert res.json()["url"] == "https://checkout.stripe.test/session"
    assert stripe_fake.customers == [{
        "email": admin.email, "name": "Ada Admin",
        "metadata": {"organization_id": admin.org_id, "user_id": admin.user_id},
    }]
    checkout = stripe_fake.checkouts[0]
    assert checkout["customer"] == "cus_1" and checkout["organization_id"] == admin.org_id
    assert checkout["plan"].key == "solo_buyer_monthly" and checkout["trial_days"] == 7
    assert checkout["metadata"] == {"organization_id": admin.org_id, "user_id": admin.user_id, "plan": "solo_buyer_monthly"}

    await client.post("/billing/create-checkout-session", json={"plan": "solo_buyer_monthly"}, headers=admin.headers)
    assert len(stripe_fake.customers) == 1  # the customer is reused
    row = (await sql(admin_engine, "SELECT stripe_customer_id FROM organizations WHERE id = :o", o=admin.org_id)).one()
    assert row[0] == "cus_1"


async def test_checkout_has_no_trial_once_the_free_trial_is_used_up(client, stripe_fake, admin_engine):
    admin = await register_admin(client)
    await set_credits(admin_engine, admin.org_id, 0)
    await client.post("/billing/create-checkout-session", json={"plan": "solo_buyer_monthly"}, headers=admin.headers)
    assert stripe_fake.checkouts[0]["trial_days"] is None

    await set_credits(admin_engine, admin.org_id, 5)
    await expire_trial(admin_engine, admin.org_id)
    await client.post("/billing/create-checkout-session", json={"plan": "solo_buyer_monthly"}, headers=admin.headers)
    assert stripe_fake.checkouts[1]["trial_days"] is None


@pytest.mark.parametrize(
    ("account_type", "plan", "status"),
    [
        ("solo_buyer", "solo_buyer_monthly", 200), ("solo_buyer", "enterprise_buyer_monthly", 200),
        ("org_buyer", "enterprise_buyer_monthly", 200), ("supplier", "supplier_monthly", 200),
        ("supplier", "solo_buyer_monthly", 400), ("solo_buyer", "supplier_monthly", 400),
        ("org_buyer", "free_forever", 400),
    ],
)
async def test_plans_are_limited_to_the_right_account_types(client, stripe_fake, account_type, plan, status):
    admin = await register_admin(client, account_type=account_type)
    res = await client.post("/billing/create-checkout-session", json={"plan": plan}, headers=admin.headers)
    assert res.status_code == status, res.text


async def test_only_administrators_can_start_checkout_or_open_the_portal(client, stripe_fake):
    admin = await register_admin(client)
    buyer = await add_member(client, admin, "buyer@acme.test", "buyer")
    for path, body in (("/billing/create-checkout-session", {"plan": "solo_buyer_monthly"}), ("/billing/create-portal-session", None)):
        assert (await client.post(path, json=body, headers=buyer.headers)).status_code == 403
    assert (await client.get("/billing", headers=buyer.headers)).json()["can_manage"] is False
    assert stripe_fake.customers == []


async def test_portal_needs_a_billing_profile_and_subscribers_cannot_check_out_twice(client, stripe_fake):
    admin = await register_admin(client)
    assert (await client.post("/billing/create-portal-session", headers=admin.headers)).status_code == 409
    await client.post("/billing/create-checkout-session", json={"plan": "solo_buyer_monthly"}, headers=admin.headers)
    await subscribe(client, admin)
    res = await client.post("/billing/create-portal-session", headers=admin.headers)
    assert res.status_code == 200 and res.json()["url"] == "https://billing.stripe.test/portal"
    assert stripe_fake.portals[0]["customer"] == "cus_1"
    again = await client.post("/billing/create-checkout-session", json={"plan": "solo_buyer_monthly"}, headers=admin.headers)
    assert again.status_code == 409 and again.json()["error"]["code"] == "already_subscribed"


async def test_checkout_reports_when_stripe_is_not_configured(client):
    admin = await register_admin(client)
    res = await client.post("/billing/create-checkout-session", json={"plan": "solo_buyer_monthly"}, headers=admin.headers)
    assert res.status_code == 503 and res.json()["error"]["code"] == "billing_unavailable"


async def test_billing_summary_lists_plans_costs_and_transactions(client, worker):
    admin = await register_admin(client, account_type="supplier")
    set_ai_service(AIService([FakeProvider([good_analysis()])]))
    await upload(client, worker, admin)
    body = (await client.get("/billing", headers=admin.headers)).json()
    assert [p["key"] for p in body["plans"]] == ["supplier_monthly"]
    assert body["credit_balance"] == 9 and body["subscription_status"] == "none" and body["ai_access"] is True
    assert [(t["kind"], t["delta"]) for t in body["transactions"]] == [("usage", -1), ("trial_grant", 10)]
    assert body["costs"]["manifest_extraction"]["flat_under_items"] == 500


# --- Webhooks ------------------------------------------------------------------------------------------
async def test_webhook_rejects_missing_wrong_and_stale_signatures(client, admin_engine):
    admin = await register_admin(client)
    event = {"id": "evt_bad", "type": "customer.subscription.deleted", "data": {"object": {"id": "sub_1", "metadata": {"organization_id": admin.org_id}}}}
    payload, good = signed(event)
    cases = [
        {"content-type": "application/json"},  # no signature
        signed(event, secret="whsec_someone_else")[1],  # wrong secret
        signed(event, timestamp=int(time.time()) - 3600)[1],  # replayed an hour later
    ]
    for headers in cases:
        res = await client.post("/webhooks/stripe", content=payload, headers=headers)
        assert res.status_code == 400, res.text
    tampered = payload.replace(b"sub_1", b"sub_2")
    assert (await client.post("/webhooks/stripe", content=tampered, headers=good)).status_code == 400
    assert await balance(client, admin) == 10
    assert (await sql(admin_engine, "SELECT count(*) FROM stripe_events")).scalar() == 0


async def test_paid_checkout_activates_the_plan_and_allocates_the_monthly_quota(client, admin_engine):
    admin = await register_admin(client, account_type="solo_buyer", org=None)
    assert await subscribe(client, admin) == "processed"
    body = await me(client, admin)
    assert (body["plan"], body["subscription_status"], body["credit_balance"]) == ("solo_buyer_monthly", "active", 25)
    kinds = [(t[0], t[2], t[3]) for t in await transactions(admin_engine, admin.org_id)]
    assert kinds == [("trial_grant", 10, 10), ("expiry", -10, 0), ("plan_grant", 25, 25)]  # trial credits do not stack


async def test_enterprise_checkout_grants_250_credits_and_turns_a_solo_account_into_a_team(client):
    admin = await register_admin(client, account_type="solo_buyer", org=None)
    await subscribe(client, admin, plan="enterprise_buyer_monthly")
    body = await me(client, admin)
    assert (body["account_type"], body["credit_balance"]) == ("org_buyer", 250)
    invite = await client.post("/invitations", json={"email": "x@acme.test", "role": "buyer"}, headers=admin.headers)
    assert invite.status_code == 201, invite.text


async def test_checkout_with_a_trial_keeps_trial_credits_until_the_first_payment(client):
    admin = await register_admin(client)
    await deliver(client, "checkout.session.completed", {
        "id": "cs_1", "mode": "subscription", "client_reference_id": admin.org_id, "customer": "cus_1",
        "subscription": "sub_1", "payment_status": "no_payment_required", "invoice": "in_0",
        "metadata": {"organization_id": admin.org_id, "plan": "solo_buyer_monthly"},
    })
    await deliver(client, "invoice.payment_succeeded", invoice(invoice_id="in_0", amount=0))  # the zero trial invoice
    body = await me(client, admin)
    assert (body["subscription_status"], body["credit_balance"], body["ai_access"]) == ("trialing", 10, True)

    await deliver(client, "invoice.payment_succeeded", invoice(invoice_id="in_1"))  # trial over, first charge
    body = await me(client, admin)
    assert (body["subscription_status"], body["credit_balance"]) == ("active", 25)


async def test_each_event_is_applied_once_even_when_stripe_redelivers_it(client, admin_engine):
    admin = await register_admin(client)
    await subscribe(client, admin)
    await set_credits(admin_engine, admin.org_id, 3)
    assert await deliver(client, "invoice.payment_succeeded", invoice(), event_id="evt_cycle") == "processed"
    assert await balance(client, admin) == 25
    await set_credits(admin_engine, admin.org_id, 4)
    assert await deliver(client, "invoice.payment_succeeded", invoice(), event_id="evt_cycle") == "duplicate"
    assert await balance(client, admin) == 4


async def test_invoice_and_checkout_for_the_same_payment_grant_the_quota_once_in_either_order(client):
    admin = await register_admin(client)
    await sql_customer(client, admin)
    await deliver(client, "invoice.payment_succeeded", invoice(invoice_id="in_1"))  # arrives first
    assert await balance(client, admin) == 25
    await subscribe(client, admin, invoice="in_1")
    body = await me(client, admin)
    assert (body["credit_balance"], body["subscription_status"], body["plan"]) == (25, "active", "solo_buyer_monthly")


async def sql_customer(client, account, customer="cus_1"):
    """The checkout endpoint stores the Stripe customer before any event can arrive."""
    fake = FakeStripe()
    set_stripe(fake)
    res = await client.post("/billing/create-checkout-session", json={"plan": "solo_buyer_monthly"}, headers=account.headers)
    assert res.status_code == 200, res.text
    assert fake.checkouts[0]["customer"] == customer


async def test_renewal_refills_the_quota_and_unused_credits_do_not_roll_over(client, admin_engine):
    admin = await register_admin(client)
    await subscribe(client, admin)
    await set_credits(admin_engine, admin.org_id, 6)
    await deliver(client, "invoice.payment_succeeded", invoice(invoice_id="in_2", period_end=1_900_000_000))
    assert await balance(client, admin) == 25
    row = (await sql(admin_engine, "SELECT extract(epoch from current_period_end)::bigint FROM organizations WHERE id = :o", o=admin.org_id)).one()
    assert row[0] == 1_900_000_000
    kinds = [(t[0], t[2]) for t in await transactions(admin_engine, admin.org_id)][-2:]
    assert kinds == [("expiry", -6), ("plan_grant", 25)]


async def test_failed_payment_pauses_ai_and_emails_the_administrators_until_payment_succeeds(client, worker):
    admin = await register_admin(client)
    await add_member(client, admin, "buyer@acme.test", "buyer")
    await subscribe(client, admin)
    mail.outbox.clear()
    await deliver(client, "invoice.payment_failed", invoice(invoice_id="in_fail"))
    body = await me(client, admin)
    assert (body["subscription_status"], body["ai_access"], body["credit_balance"]) == ("past_due", False, 25)
    assert [(m["to"], m["subject"]) for m in mail.outbox] == [(admin.email, "Your payment failed")]

    provider = FakeProvider([good_analysis()])
    set_ai_service(AIService([provider]))
    _, doc = await upload(client, worker, admin)
    assert doc["analysis_status"] == "skipped" and provider.requests == []

    await deliver(client, "invoice.payment_succeeded", invoice(invoice_id="in_retry"))
    body = await me(client, admin)
    assert (body["subscription_status"], body["ai_access"]) == ("active", True)


async def test_cancellation_revokes_access_and_wipes_remaining_credits(client, worker, admin_engine):
    admin = await register_admin(client)
    await subscribe(client, admin)
    await deliver(client, "customer.subscription.deleted", {"id": "sub_1", "customer": "cus_1", "status": "canceled"})
    body = await me(client, admin)
    assert (body["plan"], body["subscription_status"], body["credit_balance"], body["ai_access"]) == (None, "cancelled", 0, False)
    assert (await transactions(admin_engine, admin.org_id))[-1][:4] == ("expiry", None, -25, 0)

    provider = FakeProvider([good_analysis()])
    set_ai_service(AIService([provider]))
    _, doc = await upload(client, worker, admin)
    assert doc["analysis_status"] == "skipped" and provider.requests == []


async def test_plan_change_in_the_portal_is_picked_up(client):
    admin = await register_admin(client, account_type="solo_buyer", org=None)
    await subscribe(client, admin)
    await deliver(client, "customer.subscription.updated", {
        "id": "sub_1", "customer": "cus_1", "status": "active", "current_period_end": 1_900_000_000,
        "items": {"data": [{"price": {"id": "price_y", "lookup_key": "enterprise_buyer_monthly"}}]},
    })
    body = await me(client, admin)
    assert (body["plan"], body["account_type"]) == ("enterprise_buyer_monthly", "org_buyer")


async def test_events_for_unknown_customers_and_unknown_types_change_nothing(client):
    admin = await register_admin(client)
    assert await deliver(client, "invoice.payment_succeeded", invoice(customer="cus_stranger")) == "processed"
    assert await deliver(client, "charge.refunded", {"id": "ch_1"}) == "ignored"
    assert await balance(client, admin) == 10


async def test_one_accounts_webhook_never_touches_another_account(client):
    a = await register_admin(client, email="a@acme.test", org="Acme")
    b = await register_admin(client, email="b@globex.test", org="Globex")
    await subscribe(client, a)
    assert (await me(client, b))["subscription_status"] == "none" and await balance(client, b) == 10
    assert (await client.get("/billing", headers=b.headers)).json()["transactions"][0]["kind"] == "trial_grant"


# --- Schema --------------------------------------------------------------------------------------------
async def test_application_can_change_a_transactions_state_but_never_its_amount(client):
    from sqlalchemy.exc import ProgrammingError

    admin = await register_admin(client)
    async with SessionLocal() as db:
        await set_tenant(db, admin.org_id)
        await db.execute(text("UPDATE credit_transactions SET state = 'settled'"))
        with pytest.raises(ProgrammingError):
            await db.execute(text("UPDATE credit_transactions SET delta = 1000, balance_after = 1000"))
