"""Masked RFQ negotiations: offers, counteroffers, decisions, messages, scrubbing, audit trail and release of
contact details on acceptance."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app import email as mail
from app.db import SessionLocal, set_tenant
from tests.helpers import add_member
from tests.market_helpers import (
    LISTING,
    SELLER_EMAIL,
    SELLER_ORG,
    buyer,
    listed_lot,
    offer,
    publish,
    supplier,
    upload,
)
from tests.test_marketplace import without_ids_and_times

BUYER_ORG = "FlipCo Resale"
BUYER_EMAIL = "buyer@flipco.test"


async def start(client, worker, price="1000.00", **extra):
    seller, lot_id = await listed_lot(client, worker)
    shopper = await buyer(client)
    mail.outbox.clear()
    res = await client.post(f"/marketplace/lots/{lot_id}/offers", json=offer(price, **extra), headers=shopper.headers)
    assert res.status_code == 201, res.text
    return seller, shopper, lot_id, res.json()


async def thread(client, account, thread_id):
    res = await client.get(f"/rfq-threads/{thread_id}", headers=account.headers)
    assert res.status_code == 200, res.text
    return res.json()


async def act(client, account, thread_id, action, body=None, status=200):
    res = await client.post(f"/rfq-threads/{thread_id}/{action}", json=body, headers=account.headers)
    assert res.status_code == status, res.text
    return res.json()


# --- Offers & masked notification ---------------------------------------------------------------------------
async def test_an_offer_opens_a_thread_and_the_seller_gets_a_masked_notification(client, worker):
    seller, shopper, lot_id, t = await start(client, worker, custom_terms="Need it by Friday")
    assert (t["status"], t["my_side"], t["offer_by"], t["awaiting"]) == ("open", "buyer", "buyer", "them")
    assert t["counterparty"]["alias"].startswith("S-") and t["events"][0]["kind"] == "offer"
    assert t["events"][0]["offer"]["price"] == "1000.00" and t["chain_valid"] is True

    assert [m["to"] for m in mail.outbox] == [SELLER_EMAIL]
    note = mail.outbox[0]
    assert note["subject"] == f"New offer on your lot: {LISTING['title']}"
    assert "$1,000.00" in note["body"] and f"/rfq/{t['id']}" in note["body"] and "B-" in note["body"]
    for secret in (BUYER_ORG, BUYER_EMAIL, "FlipCo", shopper.org_id):
        assert secret not in note["body"], secret

    seen = await thread(client, seller, t["id"])
    assert (seen["my_side"], seen["awaiting"]) == ("supplier", "you")
    assert seen["counterparty"]["label"] == "Reseller" and seen["revealed"] is None
    raw = (await client.get(f"/rfq-threads/{t['id']}", headers=seller.headers)).text
    assert BUYER_ORG not in raw and BUYER_EMAIL not in raw


async def test_one_open_negotiation_per_buyer_and_lot(client, worker):
    _, shopper, lot_id, t = await start(client, worker)
    res = await client.post(f"/marketplace/lots/{lot_id}/offers", json=offer("1100.00"), headers=shopper.headers)
    assert res.status_code == 409 and res.json()["error"]["thread_id"] == t["id"]


@pytest.mark.parametrize("change", [{"offered_price": "0"}, {"target_close_date": "2020-01-01"},
                                    {"shipping_terms": "teleport"}])
async def test_invalid_offers_are_refused(client, worker, change):
    _, lot_id = await listed_lot(client, worker)
    shopper = await buyer(client)
    res = await client.post(f"/marketplace/lots/{lot_id}/offers", json={**offer(), **change}, headers=shopper.headers)
    assert res.status_code in (400, 422)


async def test_an_offer_below_the_reserve_is_declined_without_revealing_it(client, worker):
    seller, shopper, lot_id, t = await start(client, worker, price="850.00")  # reserve is 900
    assert t["status"] == "declined" and t["closed_reason"] == "below_reserve"
    assert [e["kind"] for e in t["events"]] == ["offer", "auto_decline"]
    assert "900" not in str(without_ids_and_times(t)) and t["warnings"]
    assert mail.outbox == []  # the seller is not bothered
    again = await client.post(f"/marketplace/lots/{lot_id}/offers", json=offer("950.00"), headers=shopper.headers)
    assert again.status_code == 201 and again.json()["status"] == "open"


# --- State machine -----------------------------------------------------------------------------------------
async def test_counteroffers_alternate_and_only_the_facing_party_can_decide(client, worker):
    seller, shopper, _, t = await start(client, worker)
    tid = t["id"]
    await act(client, shopper, tid, "accept", status=409)  # cannot accept your own offer
    await act(client, shopper, tid, "counter", {"offered_price": "1050.00"}, status=409)  # wait for an answer
    await act(client, seller, tid, "withdraw", status=409)  # not the seller's offer

    mail.outbox.clear()
    countered = await act(client, seller, tid, "counter", {"offered_price": "1150.00", "shipping_terms": "seller_freight"})
    assert (countered["offer_by"], countered["offer_price"], countered["shipping_terms"]) == ("supplier", "1150.00", "seller_freight")
    assert [m["to"] for m in mail.outbox] == [BUYER_EMAIL]
    assert SELLER_ORG not in mail.outbox[0]["body"] and "S-" in mail.outbox[0]["body"]

    back = await act(client, shopper, tid, "counter", {"offered_price": "1100.00"})
    assert back["target_close_date"] == t["target_close_date"]  # unchanged terms carry over
    assert [e["kind"] for e in back["events"]] == ["offer", "counter", "counter"]


async def test_a_buyer_counter_below_the_reserve_is_declined(client, worker):
    seller, shopper, _, t = await start(client, worker)
    await act(client, seller, t["id"], "counter", {"offered_price": "1150.00"})
    out = await act(client, shopper, t["id"], "counter", {"offered_price": "700.00"})
    assert out["status"] == "declined" and out["closed_reason"] == "below_reserve"


async def test_decline_and_withdraw_close_the_thread(client, worker):
    seller, shopper, lot_id, t = await start(client, worker)
    out = await act(client, seller, t["id"], "decline", {"note": "Too low"})
    assert out["status"] == "declined" and out["events"][-1]["body"] == "Too low"
    await act(client, seller, t["id"], "counter", {"offered_price": "1"}, status=409)

    second = await client.post(f"/marketplace/lots/{lot_id}/offers", json=offer("1100.00"), headers=shopper.headers)
    out = await act(client, shopper, second.json()["id"], "withdraw")
    assert out["status"] == "withdrawn"


async def test_acceptance_sells_the_lot_releases_contacts_and_closes_rival_offers(client, worker):
    seller, shopper, lot_id, t = await start(client, worker, "1100.00")
    rival = await buyer(client, email="rival@resell.test", org="Rival Resale")
    rival_thread = (await client.post(f"/marketplace/lots/{lot_id}/offers", json=offer("1000.00"),
                                      headers=rival.headers)).json()
    mail.outbox.clear()

    done = await act(client, seller, t["id"], "accept")
    assert (done["status"], done["agreed_price"], done["awaiting"]) == ("accepted", "1100.00", None)
    seller_view = done["revealed"]
    assert seller_view["buyer"]["company"] == BUYER_ORG and seller_view["buyer"]["contact"]["email"] == BUYER_EMAIL

    buyer_view = (await thread(client, shopper, t["id"]))["revealed"]
    assert buyer_view["seller"]["company"] == SELLER_ORG
    assert buyer_view["seller"]["pickup_address"] == LISTING["pickup_address"]
    assert buyer_view["seller"]["pickup_phone"] == LISTING["pickup_phone"]

    closed = await thread(client, rival, rival_thread["id"])
    assert (closed["status"], closed["closed_reason"], closed["revealed"]) == ("closed", "lot_sold", None)
    assert closed["events"][-1]["body"] == "This lot was sold to another buyer."
    recipients = sorted(m["to"] for m in mail.outbox)
    assert recipients == sorted([BUYER_EMAIL, "rival@resell.test"])

    assert (await client.get("/marketplace/lots", headers=rival.headers)).json()["total"] == 0
    lot = (await client.get(f"/supplier/lots/{lot_id}", headers=seller.headers)).json()
    assert lot["status"] == "sold"
    profile = (await client.get(f"/marketplace/lots/{lot_id}", headers=shopper.headers)).json()["seller"]
    assert profile["completed_deals"] == 1 and "1 Lot Completed" in profile["badges"]

    await act(client, shopper, t["id"], "decline", status=409)  # final
    msg = await act(client, shopper, t["id"], "messages", {"body": "Booking freight for Tuesday"}, status=201)
    assert msg["events"][-1]["body"] == "Booking freight for Tuesday"  # coordination continues after the deal


async def test_withdrawing_a_lot_closes_open_negotiations(client, worker):
    seller, shopper, lot_id, t = await start(client, worker)
    await client.post(f"/supplier/lots/{lot_id}/withdraw", headers=seller.headers)
    closed = await thread(client, shopper, t["id"])
    assert (closed["status"], closed["closed_reason"], closed["lot_title"]) == ("closed", "lot_withdrawn", LISTING["title"])
    assert any(t["id"] == closed["id"] for t in (await client.get("/rfq-threads", headers=shopper.headers)).json())


# --- Messages & scrubbing ----------------------------------------------------------------------------------
async def test_messages_have_contact_details_and_payment_links_removed(client, worker, admin_engine):
    seller, shopper, _, t = await start(client, worker)
    raw = ("Can you load Monday? Email me at sam@flipco.test or call 312-555-0188. "
           "Happy to pay via https://paypal.me/flipco to skip fees.")
    out = await act(client, shopper, t["id"], "messages", {"body": raw}, status=201)
    event = out["events"][-1]
    assert event["body"] == ("Can you load Monday? Email me at [email removed] or call [phone removed]. "
                             "Happy to pay via [payment link removed] to skip fees.")
    assert event["flags"] == ["email", "payment_link", "phone"]
    assert len(out["warnings"]) == 3
    seller_view = (await thread(client, seller, t["id"]))["events"][-1]
    assert seller_view["body"] == event["body"] and seller_view["mine"] is False

    async with SessionLocal() as db:  # the original is kept for the author's account only
        await set_tenant(db, shopper.org_id)
        assert (await db.execute(text("SELECT original FROM rfq_moderation"))).scalar() == raw
        await set_tenant(db, seller.org_id)
        assert (await db.execute(text("SELECT count(*) FROM rfq_moderation"))).scalar() == 0


async def test_custom_terms_and_decision_notes_are_scrubbed_too(client, worker):
    seller, shopper, _, t = await start(client, worker, custom_terms="Wire to my bank, details at flipco.shop")
    assert t["custom_terms"] == "Wire to my bank, details at [link removed]" and t["events"][0]["flags"] == ["link"]
    out = await act(client, seller, t["id"], "decline", {"note": f"No thanks - {SELLER_ORG}, 312-555-0142"})
    assert SELLER_ORG not in out["events"][-1]["body"] and "555-0142" not in out["events"][-1]["body"]


async def test_off_platform_payment_mentions_are_flagged(client, worker):
    _, shopper, _, t = await start(client, worker)
    out = await act(client, shopper, t["id"], "messages", {"body": "Can I just send it with Zelle?"}, status=201)
    assert out["events"][-1]["body"] == "Can I just send it with Zelle?"
    assert out["events"][-1]["flags"] == ["payment_off_platform"] and out["warnings"]


# --- Privacy, roles and audit trail ------------------------------------------------------------------------
async def test_negotiations_are_invisible_to_everyone_else(client, worker):
    _, _, _, t = await start(client, worker)
    outsider = await buyer(client, email="nosy@resell.test", org="Nosy Resale")
    other_seller = await supplier(client, email="x@otherliq.test", org="Other Liquidators")
    for account in (outsider, other_seller):
        assert (await client.get(f"/rfq-threads/{t['id']}", headers=account.headers)).status_code == 404
        assert (await client.get("/rfq-threads", headers=account.headers)).json() == []
        await act(client, account, t["id"], "messages", {"body": "hi"}, status=404)
    async with SessionLocal() as db:
        await set_tenant(db, outsider.org_id)
        assert (await db.execute(text("SELECT count(*) FROM rfq_threads"))).scalar() == 0
        assert (await db.execute(text("SELECT count(*) FROM rfq_thread_events"))).scalar() == 0


async def test_the_negotiation_record_is_append_only_and_tamper_evident(client, worker, admin_engine):
    seller, shopper, _, t = await start(client, worker)
    await act(client, seller, t["id"], "counter", {"offered_price": "1150.00"})
    await act(client, shopper, t["id"], "accept")
    events = (await thread(client, shopper, t["id"]))["events"]
    assert [e["kind"] for e in events] == ["offer", "counter", "accept"]
    assert events[-1]["offer"]["price"] == "1150.00"

    async with SessionLocal() as db:
        await set_tenant(db, shopper.org_id)
        for statement in ("UPDATE rfq_thread_events SET body = 'x'", "DELETE FROM rfq_thread_events"):
            with pytest.raises(ProgrammingError):
                await db.execute(text(statement))
            await db.rollback()
            await set_tenant(db, shopper.org_id)

    async with admin_engine.begin() as conn:  # someone with database access rewrites history
        await conn.execute(text("UPDATE rfq_thread_events SET offer = jsonb_set(offer, '{price}', '\"900.00\"') "
                                "WHERE kind = 'counter'"))
    assert (await thread(client, shopper, t["id"]))["chain_valid"] is False


async def test_a_requester_can_read_but_not_negotiate(client, worker):
    seller, lot_id = await listed_lot(client, worker)
    team = await register_team(client)
    requester = await add_member(client, team, "req@buyteam.test", "requester")
    res = await client.post(f"/marketplace/lots/{lot_id}/offers", json=offer(), headers=requester.headers)
    assert res.status_code == 403
    member = await add_member(client, team, "trader@buyteam.test", "buyer")
    res = await client.post(f"/marketplace/lots/{lot_id}/offers", json=offer(), headers=member.headers)
    assert res.status_code == 201
    assert (await client.get(f"/rfq-threads/{res.json()['id']}", headers=requester.headers)).status_code == 200


async def register_team(client):
    from tests.helpers import register_admin

    return await register_admin(client, email="lead@buyteam.test", org="Buy Team Inc", account_type="org_buyer")


# --- The whole journey ----------------------------------------------------------------------------------------
async def test_end_to_end_supplier_upload_to_masked_offer_notification(client, worker, admin_engine):
    from app.ai.service import AIService, set_ai_service
    from tests.fakes import FakeProvider

    def model(request):
        if "manifest_rows" == request.schema_name:
            return {"rows": [{"row": 3, "title": "DeWalt 20V MAX Cordless Drill Kit DCD771C2", "brand": "DeWalt",
                              "category": "tools", "condition": "customer_return", "upc": None, "asin": None,
                              "model_number": "DCD771C2"}]}
        return {"items": [{"position": 1, "resale_unit": 55.0, "velocity": "fast"}]}

    set_ai_service(AIService([FakeProvider([model])]))
    # 1. The supplier uploads a raw manifest and it is normalized (with the model, one credit).
    seller = await supplier(client)
    lot = await upload(client, worker, seller)
    assert lot["ingest_status"] == "ready" and lot["ingest_method"] == "ai"
    assert lot["items"][0]["title"] == "DeWalt 20V MAX Cordless Drill Kit DCD771C2"
    # 2. The supplier completes the listing and publishes it.
    await publish(client, seller, lot["id"])
    # 3. The lot appears in the buyer marketplace, masked.
    shopper = await buyer(client)
    listing = (await client.get("/marketplace/lots", params={"category": "tools"}, headers=shopper.headers)).json()
    assert [entry["id"] for entry in listing["lots"]] == [lot["id"]]
    assert SELLER_ORG not in str(listing)
    # 4. The buyer spends one credit on a deep analysis.
    res = await client.post(f"/marketplace/lots/{lot['id']}/analyses", json={"channel": "amazon"}, headers=shopper.headers)
    assert res.status_code == 202
    await worker.drain()
    analysis = (await client.get(f"/marketplace/analyses/{res.json()['id']}", headers=shopper.headers)).json()
    assert analysis["status"] == "succeeded" and analysis["result"]["items"][0]["resale_source"] == "ai"
    assert (await client.get("/auth/me", headers=shopper.headers)).json()["credit_balance"] == 9
    # 5. The buyer submits an offer.
    mail.outbox.clear()
    res = await client.post(f"/marketplace/lots/{lot['id']}/offers",
                            json=offer("1000.00", shipping_terms="seller_freight"), headers=shopper.headers)
    assert res.status_code == 201 and res.json()["status"] == "open"
    # 6. The supplier receives a masked notification and sees the offer waiting.
    assert [m["to"] for m in mail.outbox] == [SELLER_EMAIL]
    assert BUYER_ORG not in mail.outbox[0]["body"] and BUYER_EMAIL not in mail.outbox[0]["body"]
    inbox = (await client.get("/rfq-threads", headers=seller.headers)).json()
    assert [(x["id"], x["awaiting"], x["counterparty"]["label"]) for x in inbox] == [(res.json()["id"], "you", "Reseller")]
