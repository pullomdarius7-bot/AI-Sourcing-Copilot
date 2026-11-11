"""The AI sourcing engine: marketplace first, then grounded web supplier discovery, supplier invitations with
safeguards, the supplier lead feed and proposals."""
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app import email as mail
from app.ai.service import AIService, set_ai_service
from app.config import get_settings
from app.db import SessionLocal, set_tenant
from app.sourcing import web
from app.sourcing.search import SearchError, set_search
from app.sourcing.service import set_http_client
from tests.fakes import FakeProvider
from tests.helpers import register_admin
from tests.market_helpers import buyer, listed_lot, supplier, upload
from tests.web_fakes import FakeSearch, acme_profile, web_client

settings = get_settings()
REQUEST = {"title": "Cordless drill kits", "description": "20V drill kits for resale", "category": "tools",
           "quantity": 500, "target_unit_price": "60.00", "region": "midwest"}


@pytest.fixture
def fake_web(monkeypatch):
    monkeypatch.setattr(settings, "web_allow_private_hosts", True)  # the fake hosts have no DNS
    monkeypatch.setattr(settings, "invite_sender_postal_address", "1 Market St, Suite 100, San Francisco, CA 94105")
    search = FakeSearch()
    set_search(search)
    log: list[str] = []
    set_http_client(web_client(log=log))
    return search, log


async def me(client, account):
    return (await client.get("/auth/me", headers=account.headers)).json()


async def new_request(client, worker, account, **overrides):
    res = await client.post("/sourcing/requests", json={**REQUEST, **overrides}, headers=account.headers)
    assert res.status_code == 201, res.text
    await worker.drain()
    detail = await client.get(f"/sourcing/requests/{res.json()['id']}", headers=account.headers)
    return detail.json()


def web_found(detail):
    return [c for c in detail["candidates"] if c["kind"] == "web"]


# --- Marketplace first ------------------------------------------------------------------------------------
async def test_a_matching_marketplace_lot_is_offered_first_and_the_web_is_not_searched(client, worker, fake_web):
    search, _ = fake_web
    _, lot_id = await listed_lot(client, worker)  # contains a DeWalt cordless drill kit
    shopper = await buyer(client)
    detail = await new_request(client, worker, shopper, title="DeWalt cordless drill")
    assert [(c["kind"], c["lot_id"]) for c in detail["candidates"]] == [("marketplace", lot_id)]
    assert detail["web_status"] == "none" and "marketplace" in detail["web_note"]
    assert search.queries == []
    assert (await me(client, shopper))["credit_balance"] == 10


async def test_the_buyer_can_search_the_web_anyway(client, worker, fake_web):
    search, _ = fake_web
    await listed_lot(client, worker)
    shopper = await buyer(client)
    detail = await new_request(client, worker, shopper, title="DeWalt cordless drill")
    res = await client.post(f"/sourcing/requests/{detail['id']}/search-web", headers=shopper.headers)
    assert res.status_code == 202, res.text
    await worker.drain()
    detail = (await client.get(f"/sourcing/requests/{detail['id']}", headers=shopper.headers)).json()
    assert detail["web_status"] == "done" and web_found(detail)


# --- Web discovery ------------------------------------------------------------------------------------------
async def test_with_no_marketplace_match_the_web_is_searched_and_suppliers_are_organized(client, worker, fake_web, admin_engine):
    search, log = fake_web
    shopper = await buyer(client)
    mail.outbox.clear()
    detail = await new_request(client, worker, shopper)
    assert detail["marketplace_matches"] == 0 and detail["web_status"] == "done", detail
    assert search.queries[0] == "Cordless drill kits wholesale supplier"
    found = {c["domain"]: c for c in web_found(detail)}
    assert set(found) == {"acmetools.example", "beanbulk.example"}  # blog, marketplace, robots-blocked, PDF skipped
    acme = found["acmetools.example"]
    assert acme["company_name"] == "Acme Tool Wholesale" and acme["method"] == "rules"
    assert acme["contact_email"] == "sales@acmetools.example" and acme["contact_url"] == "https://acmetools.example/contact"
    assert acme["contact_phone"] == "(312) 555-0100"
    assert any("Minimum order: 50 units." in e["quote"] for e in acme["evidence"])
    assert found["beanbulk.example"]["contact_email"] == "beanbulk@gmail.com"
    assert not any("amazon" in url or url.startswith("https://norobots.example/") and not url.endswith("robots.txt")
                   for url in log)
    assert (await me(client, shopper))["credit_balance"] == 8  # a web search costs 2 credits
    assert any(m["subject"] == "Sourcing results: Cordless drill kits" for m in mail.outbox)


async def test_the_model_profiles_suppliers_but_only_with_what_their_site_states(client, worker, fake_web):
    def answer(request):
        if "acmetools.example" in request.user:
            return acme_profile(contact_email="ceo@acmetools.example")  # not published: replaced
        return acme_profile(company_name="Bean Bulk Liquidation", is_supplier=True, relevance=0.2, products=[])

    set_ai_service(AIService([FakeProvider([answer])]))
    shopper = await buyer(client)
    detail = await new_request(client, worker, shopper)
    found = {c["domain"]: c for c in web_found(detail)}
    assert set(found) == {"acmetools.example"}  # the other site was not relevant enough
    acme = found["acmetools.example"]
    assert acme["method"] == "ai" and acme["business_type"] == "wholesaler" and acme["location"] == "Chicago, IL"
    assert [p["name"] for p in acme["products"]] == ["DeWalt 20V MAX drill kits"]
    assert acme["products"][0]["url"] == "https://acmetools.example/"
    assert acme["contact_email"] == "sales@acmetools.example"
    assert {"ai_product_rejected", "ai_email_rejected"} <= set(acme["flags"])


async def test_nothing_found_means_no_charge(client, worker, fake_web):
    search, _ = fake_web
    search.urls = ["https://drillblog.example/best-drills"]
    shopper = await buyer(client)
    detail = await new_request(client, worker, shopper)
    assert detail["web_status"] == "done" and web_found(detail) == []
    assert "refunded" in detail["web_note"]
    assert (await me(client, shopper))["credit_balance"] == 10


async def test_a_failing_search_service_charges_nothing(client, worker, fake_web):
    search, _ = fake_web
    search.error = SearchError("fake returned HTTP 503")
    shopper = await buyer(client)
    detail = await new_request(client, worker, shopper)
    assert detail["web_status"] == "failed" and (await me(client, shopper))["credit_balance"] == 10


async def test_without_a_search_provider_the_request_still_works(client, worker):
    shopper = await buyer(client)
    detail = await new_request(client, worker, shopper)
    assert detail["web_status"] == "skipped" and "not configured" in detail["web_note"]
    assert detail["web_search_available"] is False
    res = await client.post(f"/sourcing/requests/{detail['id']}/search-web", headers=shopper.headers)
    assert res.status_code == 409


async def test_without_credits_the_web_search_is_skipped_or_refused(client, worker, fake_web, admin_engine):
    shopper = await buyer(client)
    async with admin_engine.begin() as conn:
        await conn.execute(text("UPDATE organizations SET credit_balance = 1 WHERE id = :o"), {"o": shopper.org_id})
    detail = await new_request(client, worker, shopper)
    assert detail["web_status"] == "skipped" and "credit" in detail["web_note"]
    res = await client.post(f"/sourcing/requests/{detail['id']}/search-web", headers=shopper.headers)
    assert res.status_code == 402 and res.json()["error"]["code"] == "INSUFFICIENT_CREDITS"


async def test_contact_details_typed_into_a_request_are_removed(client, worker, fake_web):
    shopper = await buyer(client)
    res = await client.post("/sourcing/requests", headers=shopper.headers,
                            json={**REQUEST, "description": "Call me on 415-555-0100 or bea@flipco.example"})
    assert "[phone removed]" in res.json()["description"] and res.json()["warnings"]


# --- Crawler safety -------------------------------------------------------------------------------------------
async def test_the_crawler_refuses_internal_addresses(monkeypatch):
    monkeypatch.setattr(settings, "web_allow_private_hosts", False)
    async with web.Crawler(web_client()) as crawler:
        for url in ("http://localhost/admin", "http://127.0.0.1:8000/api/v1/health", "file:///etc/passwd"):
            with pytest.raises(web.FetchError):
                await crawler.page(url)


async def test_redirects_into_the_internal_network_are_refused(monkeypatch):
    monkeypatch.setattr(settings, "web_allow_private_hosts", False)
    real = web._public_host

    async def only_redirect_host_is_public(host):
        if host == "redirect.example":
            return None
        await real(host)

    monkeypatch.setattr(web, "_public_host", only_redirect_host_is_public)
    async with web.Crawler(web_client()) as crawler:
        with pytest.raises(web.FetchError, match="non-public"):
            await crawler.page("https://redirect.example/")


def test_marketplaces_and_social_sites_are_never_crawled():
    for url in ("https://www.amazon.com/x", "https://amazon.co.uk/x", "https://smile.amazon.de/x",
                "https://www.facebook.com/acme", "https://m.alibaba.com/x"):
        assert web.blocked(url), url
    assert not web.blocked("https://acmetools.example/")
    assert not web.blocked("https://xamazon.example/")


def test_html_is_read_as_text_without_scripts():
    page = web.parse_html("https://acmetools.example/", __import__("tests.web_fakes", fromlist=["ACME_HOME"]).ACME_HOME)
    assert "do not read me" not in page.text and "Minimum order: 50 units." in page.text
    assert page.site_name == "Acme Tool Wholesale"
    assert ("https://acmetools.example/contact", "Contact us") in page.links


# --- Invitations ----------------------------------------------------------------------------------------------
def invite_mail():
    return [m for m in mail.outbox if m["subject"].startswith("Buyers are looking for")]


async def test_found_suppliers_get_one_masked_compliant_invitation(client, worker, fake_web):
    shopper = await buyer(client)
    mail.outbox.clear()
    detail = await new_request(client, worker, shopper)
    assert {c["domain"]: c["invite_status"] for c in web_found(detail)} == {
        "acmetools.example": "queued", "beanbulk.example": "queued"}
    assert detail["invites_sent"] == 2
    sent = {m["to"]: m for m in invite_mail()}
    assert set(sent) == {"sales@acmetools.example", "beanbulk@gmail.com"}
    body = sent["sales@acmetools.example"]["body"]
    assert "Cordless drill kits (500 units, Tools, delivery to the Midwest)" in body
    assert "published on acmetools.example" in body and "/unsubscribe?token=" in body
    assert "/register?role=supplier&invite=" in body and "1 Market St" in body
    for secret in ("FlipCo", "buyer@flipco.test", shopper.org_id):
        assert secret not in body


async def test_a_domain_is_emailed_once_per_cooldown_however_many_buyers_ask(client, worker, fake_web, admin_engine):
    first = await buyer(client)
    second = await buyer(client, email="b2@resell.test", org="Second Resale")
    await new_request(client, worker, first)
    mail.outbox.clear()
    detail = await new_request(client, worker, second, title="Cordless drill kits 18V")
    assert {c["invite_status"] for c in web_found(detail)} == {"invited"}
    assert invite_mail() == []
    async with admin_engine.connect() as conn:
        row = (await conn.execute(text("SELECT request_count, sent_count FROM supplier_invites "
                                       "WHERE domain = 'acmetools.example'"))).one()
    assert tuple(row) == (2, 1)


async def test_unsubscribing_stops_all_future_invitations(client, worker, fake_web):
    shopper = await buyer(client)
    await new_request(client, worker, shopper)
    token = invite_mail()[0]["body"].split("unsubscribe?token=")[1].split()[0]
    res = await client.post("/invites/unsubscribe", json={"token": token})
    assert res.status_code == 200 and res.json()["unsubscribed"] is True
    assert (await client.get("/invites/preview", params={"token": token})).status_code == 404
    mail.outbox.clear()
    other = await buyer(client, email="b2@resell.test", org="Second Resale")
    detail = await new_request(client, worker, other)
    # One business unsubscribed; the other was already emailed within the cooldown. Nobody is emailed again.
    assert sorted(c["invite_status"] for c in web_found(detail)) == ["invited", "suppressed"]
    assert invite_mail() == []


async def test_invitations_are_held_without_a_postal_address(client, worker, fake_web, monkeypatch, admin_engine):
    monkeypatch.setattr(settings, "invite_sender_postal_address", "")
    shopper = await buyer(client)
    mail.outbox.clear()
    await new_request(client, worker, shopper)
    assert invite_mail() == []
    async with admin_engine.connect() as conn:
        statuses = (await conn.execute(text("SELECT DISTINCT status FROM supplier_invites"))).scalars().all()
    assert statuses == ["held"]


@pytest.mark.parametrize("mode", ["review", "off"])
async def test_review_and_off_modes_send_nothing(client, worker, fake_web, monkeypatch, mode):
    monkeypatch.setattr(settings, "supplier_invites", mode)
    shopper = await buyer(client)
    mail.outbox.clear()
    detail = await new_request(client, worker, shopper)
    assert invite_mail() == []
    assert {c["invite_status"] for c in web_found(detail)} == ({"held"} if mode == "review" else {None})


async def test_private_requests_never_trigger_invitations(client, worker, fake_web):
    shopper = await buyer(client)
    mail.outbox.clear()
    detail = await new_request(client, worker, shopper, open_to_suppliers=False)
    assert web_found(detail) and invite_mail() == []


async def test_businesses_already_on_the_platform_are_not_invited(client, worker, fake_web):
    await register_admin(client, email="owner@acmetools.example", org="Acme Tool Wholesale", account_type="supplier")
    shopper = await buyer(client)
    detail = await new_request(client, worker, shopper)
    assert {c["domain"]: c["invite_status"] for c in web_found(detail)}["acmetools.example"] == "member"


async def test_an_invited_supplier_joins_and_answers_the_request_that_brought_it(client, worker, fake_web):
    shopper = await buyer(client)
    detail = await new_request(client, worker, shopper)
    token = next(m for m in invite_mail() if m["to"] == "sales@acmetools.example")["body"].split("invite=")[1].split()[0]
    preview = (await client.get("/invites/preview", params={"token": token})).json()
    assert preview["company_name"] == "Acme Tool Wholesale" and preview["demand"][0]["title"] == "Cordless drill kits"

    res = await client.post("/auth/register", json={
        "account_type": "supplier", "organization_name": "Acme Tool Wholesale", "full_name": "Sam Acme",
        "email": "sam@acmetools.example", "password": "correct-horse-battery-9", "invite_token": token})
    assert res.status_code == 201
    from tests.helpers import complete_onboarding, login, token_from_email

    await client.post("/auth/verify-email", json={"token": token_from_email("sam@acmetools.example")})
    acme = await login(client, "sam@acmetools.example")
    await complete_onboarding(client, acme, "supplier")

    feed = (await client.get("/supplier/requests", headers=acme.headers)).json()
    lead = next(item for item in feed if item["id"] == detail["id"])
    assert lead["quantity"] == 500 and lead["buyer"]["alias"].startswith("B-")
    raw = (await client.get("/supplier/requests", headers=acme.headers)).text
    assert "FlipCo" not in raw and "buyer@flipco.test" not in raw

    lot = await upload(client, worker, acme)
    from tests.market_helpers import publish

    await publish(client, acme, lot["id"])
    mail.outbox.clear()
    res = await client.post(f"/supplier/requests/{detail['id']}/proposals", headers=acme.headers, json={
        "lot_id": lot["id"], "offered_price": "1150.00", "shipping_terms": "seller_freight",
        "target_close_date": (date.today() + timedelta(days=5)).isoformat()})
    assert res.status_code == 201, res.text
    thread = res.json()
    assert (thread["offer_by"], thread["my_side"]) == ("supplier", "supplier")
    assert [m["to"] for m in mail.outbox] == ["buyer@flipco.test"]
    assert "Acme" not in mail.outbox[0]["body"]

    mine = (await client.get(f"/sourcing/requests/{detail['id']}", headers=shopper.headers)).json()
    assert [(t["id"], t["awaiting"]) for t in mine["proposals"]] == [(thread["id"], "you")]
    accepted = await client.post(f"/rfq-threads/{thread['id']}/accept", json={}, headers=shopper.headers)
    assert accepted.json()["status"] == "accepted"


async def test_closed_requests_leave_the_lead_feed(client, worker, fake_web):
    shopper = await buyer(client)
    detail = await new_request(client, worker, shopper)
    seller = await supplier(client)
    assert any(r["id"] == detail["id"] for r in (await client.get("/supplier/requests", headers=seller.headers)).json())
    await client.post(f"/sourcing/requests/{detail['id']}/close", headers=shopper.headers)
    assert all(r["id"] != detail["id"] for r in (await client.get("/supplier/requests", headers=seller.headers)).json())


# --- Into the buyer's workflow -----------------------------------------------------------------------------------
async def test_a_found_supplier_can_be_saved_to_the_supplier_directory(client, worker, fake_web):
    shopper = await register_admin(client, email="lead@buyteam.test", org="Buy Team Inc")
    detail = await new_request(client, worker, shopper)
    acme = next(c for c in web_found(detail) if c["domain"] == "acmetools.example")
    res = await client.post(f"/sourcing/candidates/{acme['id']}/add-to-suppliers", headers=shopper.headers)
    assert res.status_code == 200 and res.json()["status"] == "added"
    directory = (await client.get("/suppliers", headers=shopper.headers)).json()
    entry = next(s for s in directory["items"] if s["name"] == "Acme Tool Wholesale")
    supplier_detail = (await client.get(f"/suppliers/{entry['id']}", headers=shopper.headers)).json()
    assert supplier_detail["website"] == "https://acmetools.example/"
    assert supplier_detail["contacts"][0]["email"] == "sales@acmetools.example"
    again = await client.post(f"/sourcing/candidates/{acme['id']}/add-to-suppliers", headers=shopper.headers)
    assert again.json()["supplier_id"] == res.json()["supplier_id"]


async def test_sourcing_results_are_private_and_invites_are_platform_only(client, worker, fake_web):
    shopper = await buyer(client)
    detail = await new_request(client, worker, shopper)
    other = await buyer(client, email="nosy@resell.test", org="Nosy Resale")
    assert (await client.get(f"/sourcing/requests/{detail['id']}", headers=other.headers)).status_code == 404
    async with SessionLocal() as db:
        await set_tenant(db, other.org_id)
        assert (await db.execute(text("SELECT count(*) FROM sourcing_candidates"))).scalar() == 0
        assert (await db.execute(text("SELECT count(*) FROM supplier_invites"))).scalar() == 0
        await set_tenant(db, shopper.org_id)
        assert (await db.execute(text("SELECT count(*) FROM supplier_invites"))).scalar() == 0
