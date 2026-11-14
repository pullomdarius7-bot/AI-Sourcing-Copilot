"""Supplier manifest ingestion, listings, the buyer marketplace and paid deep analysis."""
import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app.ai.providers import ProviderError
from app.ai.service import AIService, set_ai_service
from app.db import SessionLocal, set_tenant
from tests.fakes import FakeProvider
from tests.helpers import add_member
from tests.market_helpers import (
    LISTING,
    MANIFEST,
    SELLER_EMAIL,
    SELLER_ORG,
    buyer,
    listed_lot,
    publish,
    supplier,
    upload,
)
from tests.samples import EICAR, make_pdf_lines


async def me(client, account):
    return (await client.get("/auth/me", headers=account.headers)).json()


async def set_credits(admin_engine, org_id, amount):
    async with admin_engine.begin() as conn:
        await conn.execute(text("UPDATE organizations SET credit_balance = :a WHERE id = :o"), {"a": amount, "o": org_id})


def without_ids_and_times(value):
    if isinstance(value, dict):
        return {k: without_ids_and_times(v) for k, v in value.items()
                if not (k == "id" or k.endswith("_id") or k.endswith("_at") or k == "member_since"
                        or "sha256" in k or k.endswith("hash"))}  # random digits that can contain any number
    if isinstance(value, list):
        return [without_ids_and_times(v) for v in value]
    return value


def by_title(lot, start):
    return next(i for i in lot["items"] if i["title"].startswith(start))


# --- Ingestion: rules ---------------------------------------------------------------------------------------
async def test_messy_csv_manifest_is_normalized_by_rules(client, worker):
    seller = await supplier(client)
    lot = await upload(client, worker, seller)
    assert (lot["ingest_status"], lot["ingest_method"], lot["status"]) == ("ready", "rules", "draft")
    assert lot["item_count"] == 4 and lot["total_units"] == 32  # the totals row is not an item
    assert float(lot["total_msrp"]) == 4569.90
    drill = by_title(lot, "DEWALT")
    assert (drill["category"], drill["condition"], drill["upc"], drill["quantity"], float(drill["unit_msrp"])) == (
        "tools", "customer_return", "885911425308", 4, 129.0)
    fryer = by_title(lot, "Ninja")
    assert (fryer["category"], fryer["condition"], fryer["brand"]) == ("appliances", "like_new", "Ninja")
    pods = by_title(lot, "Apple AirPods")
    assert (pods["category"], pods["condition"], pods["asin"]) == ("electronics", "salvage", "B0CHWRXH8B")
    shoes = by_title(lot, "Nike")
    assert (shoes["category"], shoes["condition"], float(shoes["unit_msrp"])) == ("footwear", "new", 130.0)
    assert lot["condition_mix"] == {"new": 12, "like_new": 10, "salvage": 6, "customer_return": 4}
    assert lot["title"] == "Footwear New Lot - 32 units"  # a draft title until the seller writes one
    assert all(i["method"] == "rules" for i in lot["items"])


async def test_missing_condition_column_uses_the_sellers_default(client, worker):
    seller = await supplier(client)
    data = "Description,Qty,MSRP\nKeurig K-Mini Coffee Maker,5,89.99\nGeneric phone case,20,15\n"
    lot = await upload(client, worker, seller, data=data, default_condition="like_new")
    assert {i["condition"] for i in lot["items"]} == {"like_new"}
    assert lot["items"][0]["brand"] == "Keurig" and lot["items"][0]["category"] == "appliances"


async def test_unit_msrp_is_derived_from_extended_retail(client, worker):
    seller = await supplier(client)
    data = "Product,Units,Ext Retail,Condition\nMakita impact driver,3,\"$1,050.00\",Open Box\n"
    lot = await upload(client, worker, seller, data=data)
    item = lot["items"][0]
    assert (float(item["unit_msrp"]), item["condition"], item["brand"]) == (350.0, "like_new", "Makita")


async def test_contact_details_in_manifest_titles_are_removed(client, worker):
    seller = await supplier(client)
    data = f"Description,Qty,MSRP\nCall 312-555-0199 or email sales@{SELLER_ORG.split()[0].lower()}.com for drills,1,50\n{SELLER_ORG} vacuum,1,200\n"
    lot = await upload(client, worker, seller, data=data)
    titles = " ".join(i["title"] for i in lot["items"])
    assert "555-0199" not in titles and "@" not in titles and SELLER_ORG not in titles
    assert any("contact_removed:phone" in i["flags"] for i in lot["items"])


async def test_a_file_without_a_header_row_fails_with_a_clear_reason(client, worker):
    seller = await supplier(client)
    lot = await upload(client, worker, seller, data="just,some,numbers\n1,2,3\n")
    assert lot["ingest_status"] == "failed" and "header" in lot["ingest_error"]


async def test_an_infected_manifest_is_quarantined_and_the_lot_fails(client, worker):
    seller = await supplier(client)
    lot = await upload(client, worker, seller, data=EICAR, name="manifest.csv")
    assert lot["ingest_status"] == "failed"
    assert lot["items"] == []


async def test_manifests_do_not_appear_in_the_sourcing_document_list(client, worker):
    seller = await supplier(client)
    await upload(client, worker, seller)
    assert (await client.get("/documents", headers=seller.headers)).json()["total"] == 0


# --- Ingestion: model ---------------------------------------------------------------------------------------
def normalized(rows):
    return {"rows": rows}


async def test_model_cleans_titles_but_every_value_must_be_grounded(client, worker):
    seller = await supplier(client)
    data = "Item,Qty,Retail\nDWLT 20V DRILL KIT DCD771C2 CR,2,129\nGNRC BLUETOOTH SPKR,3,40\n"

    def answer(request):
        rows = [json.loads(line) for line in request.user.split("\n") if line.startswith('{"row"')]
        assert [r["row"] for r in rows] == [2, 3]
        return normalized([
            {"row": 2, "title": "DWLT 20V Drill Kit DCD771C2", "brand": None, "category": "tools",
             "condition": "customer_return", "upc": None, "asin": None, "model_number": "DCD771C2"},
            {"row": 3, "title": "Premium Waterproof Bluetooth Speaker by Sony", "brand": "Sony", "category": "electronics",
             "condition": "new", "upc": "012345678905", "asin": None, "model_number": None},
        ])

    provider = FakeProvider([answer])
    set_ai_service(AIService([provider]))
    lot = await upload(client, worker, seller, data=data)
    assert lot["ingest_method"] == "ai" and len(provider.requests) == 1
    drill, speaker = lot["items"]
    assert (drill["title"], drill["model_number"], drill["condition"], drill["method"]) == (
        "DWLT 20V Drill Kit DCD771C2", "DCD771C2", "customer_return", "ai")
    # Invented words, brand and UPC are not in the row: rejected, the rules values stay.
    assert speaker["title"] == "GNRC BLUETOOTH SPKR" and speaker["brand"] is None and speaker["upc"] is None
    assert {"ai_title_rejected", "ai_brand_rejected", "ai_upc_rejected"} <= set(speaker["flags"])
    assert speaker["category"] == "electronics"
    assert (await me(client, seller))["credit_balance"] == 9  # one credit: fewer than 500 lines


async def test_large_manifest_costs_one_credit_per_100_lines(client, worker):
    seller = await supplier(client)
    rows = "Description,Qty,MSRP\n" + "".join(f"Widget model W{i},1,10\n" for i in range(650))
    set_ai_service(AIService([FakeProvider([normalized([])])]))
    lot = await upload(client, worker, seller, data=rows)
    assert lot["item_count"] == 650
    assert (await me(client, seller))["credit_balance"] == 3  # ceil(650 / 100) = 7


async def test_without_credits_the_manifest_is_still_normalized_by_rules(client, worker, admin_engine):
    seller = await supplier(client)
    await set_credits(admin_engine, seller.org_id, 0)
    provider = FakeProvider([normalized([])])
    set_ai_service(AIService([provider]))
    lot = await upload(client, worker, seller)
    assert lot["ingest_status"] == "ready" and lot["ingest_method"] == "rules"
    assert provider.requests == []
    assert any("AI normalization skipped" in f for f in lot["ingest_flags"])


async def test_a_failing_model_falls_back_to_rules_and_refunds(client, worker):
    seller = await supplier(client)
    set_ai_service(AIService([FakeProvider([ProviderError("bad request", retryable=False)])]))
    lot = await upload(client, worker, seller)
    assert lot["ingest_status"] == "ready" and lot["ingest_method"] == "rules"
    assert (await me(client, seller))["credit_balance"] == 10


PDF_TEXT = [
    "LIQUIDATION MANIFEST - LOAD 8812",
    "Sony WH-1000XM4 Headphones  Qty 5  MSRP 349.99  Customer Return",
    "Shark Navigator Vacuum NV352  Qty 2  MSRP 199.00  Like New",
    "Page 1 of 1",
]


async def test_pdf_manifests_are_read_by_the_model_and_checked_against_the_text(client, worker):
    seller = await supplier(client)

    def answer(_request):
        return {"lines": [
            {"source": PDF_TEXT[1], "title": "Sony WH-1000XM4 Headphones", "brand": "Sony", "category": "electronics",
             "condition": "customer_return", "upc": None, "asin": None, "model_number": "WH-1000XM4",
             "quantity": 5, "unit_msrp": 349.99, "unit_price": None},
            {"source": PDF_TEXT[2], "title": "Shark Navigator Vacuum NV352", "brand": "Shark", "category": "appliances",
             "condition": "like_new", "upc": None, "asin": None, "model_number": "NV352",
             "quantity": 2, "unit_msrp": 1990.00, "unit_price": None},  # price not in the line: dropped
            {"source": "Dyson V15 Detect  Qty 9  MSRP 749.99", "title": "Dyson V15", "brand": "Dyson",
             "category": "appliances", "condition": "new", "upc": None, "asin": None, "model_number": None,
             "quantity": 9, "unit_msrp": 749.99, "unit_price": None},  # line not in the PDF: dropped
        ]}

    set_ai_service(AIService([FakeProvider([answer])]))
    lot = await upload(client, worker, seller, data=make_pdf_lines(PDF_TEXT), name="manifest.pdf",
                       content_type="application/pdf")
    assert lot["ingest_status"] == "ready", lot
    assert [(i["title"], i["quantity"], float(i["unit_msrp"]), i["brand"]) for i in lot["items"]] == [
        ("Sony WH-1000XM4 Headphones", 5, 349.99, "Sony")]
    assert any("not found in the PDF" in f for f in lot["ingest_flags"])


async def test_pdf_manifests_need_a_model(client, worker):
    seller = await supplier(client)
    lot = await upload(client, worker, seller, data=make_pdf_lines(PDF_TEXT), name="manifest.pdf",
                       content_type="application/pdf")
    assert lot["ingest_status"] == "failed" and "CSV or XLSX" in lot["ingest_error"]


# --- Listing ------------------------------------------------------------------------------------------------
async def test_publishing_needs_the_listing_details(client, worker):
    seller = await supplier(client)
    lot = await upload(client, worker, seller)
    res = await client.post(f"/supplier/lots/{lot['id']}/publish", headers=seller.headers)
    assert res.status_code == 422
    assert res.json()["error"]["problems"] == ["Choose the lot type", "Set the Buy-It-Now price", "Enter the dispatch ZIP code"]


async def test_listing_text_is_scrubbed_and_the_seller_is_warned(client, worker):
    seller = await supplier(client)
    lot = await upload(client, worker, seller)
    res = await client.patch(f"/supplier/lots/{lot['id']}", headers=seller.headers, json={
        "title": f"{SELLER_ORG} returns pallet", "description": "Questions? Call 312-555-0199 or visit palletkings.com",
    })
    body = res.json()
    assert SELLER_ORG not in body["title"] and "555-0199" not in body["description"] and "palletkings.com" not in body["description"]
    assert len(body["warnings"]) == 3


@pytest.mark.parametrize(("change", "code"), [
    ({"reserve_price": "1500.00", "asking_price": "1200.00"}, "reserve_above_asking"),
    ({"dispatch_zip": "ABCDE"}, "validation_error"),
    ({"lot_type": "boat"}, "validation_error"),
])
async def test_invalid_listing_details_are_refused(client, worker, change, code):
    seller = await supplier(client)
    lot = await upload(client, worker, seller)
    res = await client.patch(f"/supplier/lots/{lot['id']}", json=change, headers=seller.headers)
    assert res.json()["error"]["code"] == code


async def test_listing_needs_an_active_subscription_or_trial(client, worker, admin_engine):
    seller = await supplier(client)
    lot = await upload(client, worker, seller)
    async with admin_engine.begin() as conn:
        await conn.execute(text("UPDATE organizations SET trial_ends_at = now() - interval '1 day' WHERE id = :o"),
                           {"o": seller.org_id})
    await client.patch(f"/supplier/lots/{lot['id']}", json=LISTING, headers=seller.headers)
    res = await client.post(f"/supplier/lots/{lot['id']}/publish", headers=seller.headers)
    assert res.status_code == 402 and res.json()["error"]["code"] == "SUBSCRIPTION_REQUIRED"


async def test_only_suppliers_list_and_only_buyers_shop(client, worker):
    seller = await supplier(client)
    shopper = await buyer(client)
    res = await client.post("/supplier/manifests", files={"file": ("m.csv", MANIFEST.encode(), "text/csv")},
                            headers=shopper.headers)
    assert res.status_code == 403 and res.json()["error"]["code"] == "supplier_only"
    res = await client.get("/marketplace/lots", headers=seller.headers)
    assert res.status_code == 403 and res.json()["error"]["code"] == "buyer_only"


# --- Marketplace visibility and masking ------------------------------------------------------------------------
async def test_drafts_are_private_and_published_lots_are_listed_without_seller_identity(client, worker):
    seller = await supplier(client)
    lot = await upload(client, worker, seller)
    shopper = await buyer(client)
    assert (await client.get(f"/marketplace/lots/{lot['id']}", headers=shopper.headers)).status_code == 404
    assert (await client.get("/marketplace/lots", headers=shopper.headers)).json()["total"] == 0

    await publish(client, seller, lot["id"])
    listing = (await client.get("/marketplace/lots", headers=shopper.headers)).json()
    assert listing["total"] == 1
    detail = await client.get(f"/marketplace/lots/{lot['id']}", headers=shopper.headers)
    raw = detail.text
    for secret in (SELLER_ORG, SELLER_EMAIL, seller.org_id, seller.user_id, "Fulton", "Rosa", "312-555-0142", "reserve"):
        assert secret not in raw, secret
    # Numbers are checked on the values only: random ids and timestamps may contain any digits.
    values = str(without_ids_and_times(detail.json()))
    for secret in ("900", "60607"):
        assert secret not in values, secret
    body = detail.json()
    assert body["ships_from"] == "Midwest · ZIP 606xx"
    assert body["seller"]["alias"].startswith("S-") and body["seller"]["badges"] == [
        "Liquidator - Midwest Hub", "New on the marketplace"]
    assert body["seller"]["verified"] is False
    assert len(body["items"]) == 4 and "source_line" not in body["items"][0]
    assert float(body["est_resale"]) > 0 and body["est_margin_pct"] is not None


async def test_a_paid_supplier_is_shown_as_verified(client, worker, admin_engine):
    seller = await supplier(client)
    async with admin_engine.begin() as conn:
        await conn.execute(text("UPDATE organizations SET subscription_status = 'active' WHERE id = :o"), {"o": seller.org_id})
    _, lot_id = await listed_lot(client, worker, seller)
    shopper = await buyer(client)
    body = (await client.get(f"/marketplace/lots/{lot_id}", headers=shopper.headers)).json()
    assert body["seller"]["badges"][0] == "Verified Liquidator - Midwest Hub"


async def test_rls_keeps_private_lot_details_and_drafts_from_other_accounts(client, worker):
    seller, lot_id = await listed_lot(client, worker)
    draft = await upload(client, worker, seller)
    shopper = await buyer(client)
    async with SessionLocal() as db:
        await set_tenant(db, shopper.org_id)
        visible = (await db.execute(text("SELECT id::text FROM marketplace_lots"))).scalars().all()
        assert visible == [lot_id]
        assert (await db.execute(text("SELECT count(*) FROM lot_private_details"))).scalar() == 0
        assert (await db.execute(text("SELECT count(*) FROM lot_items WHERE lot_id = :l"), {"l": draft["id"]})).scalar() == 0
        assert (await db.execute(text("SELECT count(*) FROM lot_items WHERE lot_id = :l"), {"l": lot_id})).scalar() == 4
        changed = await db.execute(text("UPDATE marketplace_lots SET asking_price = 1 WHERE id = :l"), {"l": lot_id})
        assert changed.rowcount == 0  # readable, but not writable by another account
        with pytest.raises(ProgrammingError):
            await db.execute(text(
                "INSERT INTO lot_items (organization_id, lot_id, position, title, category, condition, quantity, method) "
                f"VALUES ('{seller.org_id}', '{lot_id}', 99, 'x', 'general', 'new', 1, 'rules')"
            ))


async def test_marketplace_filters(client, worker):
    seller = await supplier(client)
    await listed_lot(client, worker, seller)  # tools/appliances/electronics/footwear, Chicago, $1,200
    data = "Description,Qty,MSRP,Condition\nLevi's 501 jeans,40,69.50,New\nHanes t-shirt 6 pack,100,24,New\n"
    apparel = await upload(client, worker, seller, data=data)
    await publish(client, seller, apparel["id"], title="Apparel overstock", lot_type="truckload",
                  asking_price="900.00", dispatch_zip="90021", reserve_price=None)
    shopper = await buyer(client)

    async def titles(**params):
        res = await client.get("/marketplace/lots", params=params, headers=shopper.headers)
        assert res.status_code == 200, res.text
        return sorted(lot["title"] for lot in res.json()["lots"])

    both = sorted([LISTING["title"], "Apparel overstock"])
    assert await titles() == both
    assert await titles(category="apparel") == ["Apparel overstock"]
    assert await titles(category=["tools", "apparel"]) == both
    assert await titles(condition="salvage") == [LISTING["title"]]
    assert await titles(lot_type="truckload") == ["Apparel overstock"]
    assert await titles(region="west_coast") == ["Apparel overstock"]
    assert await titles(region="midwest") == [LISTING["title"]]
    assert await titles(max_price=1000) == ["Apparel overstock"]
    assert await titles(q="overstock") == ["Apparel overstock"]
    margins = {lot["title"]: float(lot["est_margin_pct"]) for lot in
               (await client.get("/marketplace/lots", headers=shopper.headers)).json()["lots"]}
    threshold = (min(margins.values()) + max(margins.values())) / 2
    best = max(margins, key=margins.get)
    assert await titles(min_margin=threshold) == [best]
    ordered = (await client.get("/marketplace/lots", params={"sort": "margin"}, headers=shopper.headers)).json()["lots"]
    assert ordered[0]["title"] == best


async def test_withdrawn_lots_leave_the_marketplace(client, worker):
    seller, lot_id = await listed_lot(client, worker)
    shopper = await buyer(client)
    res = await client.post(f"/supplier/lots/{lot_id}/withdraw", headers=seller.headers)
    assert res.json()["status"] == "withdrawn"
    assert (await client.get(f"/marketplace/lots/{lot_id}", headers=shopper.headers)).status_code == 404


# --- Deep analysis --------------------------------------------------------------------------------------------
async def run_analysis(client, worker, account, lot_id, **params):
    res = await client.post(f"/marketplace/lots/{lot_id}/analyses", json=params, headers=account.headers)
    if res.status_code != 202:
        return res
    await worker.drain()
    return await client.get(f"/marketplace/analyses/{res.json()['id']}", headers=account.headers)


async def test_deep_analysis_costs_one_credit_and_returns_an_itemized_net_profit_model(client, worker, admin_engine):
    _, lot_id = await listed_lot(client, worker)
    shopper = await buyer(client)
    res = await run_analysis(client, worker, shopper, lot_id, channel="ebay", target_margin_pct=30)
    analysis = res.json()
    assert analysis["status"] == "succeeded" and analysis["method"] == "rules"
    result = analysis["result"]
    assert len(result["items"]) == 4
    drill = next(i for i in result["items"] if i["title"].startswith("DEWALT"))
    # 4 drills at $129 MSRP, customer return (30%), tools (x1.05): $40.64 each; 15% unsellable -> 3.4 units
    assert drill["resale_unit"] == 40.64 and drill["sellable_units"] == 3.4 and drill["revenue"] == 138.18
    assert drill["fees"] == round(138.18 * 0.1325, 2) and drill["resale_source"] == "rules"
    totals = result["totals"]
    assert totals["units"] == 32 and totals["cost"] == pytest.approx(1200, abs=0.05)
    assert totals["net_profit"] == pytest.approx(
        totals["revenue"] - totals["fees"] - totals["refurbishment"] - totals["freight"] - totals["cost"], abs=0.05)
    assert result["assumptions"]["freight"] == 225.0  # one pallet
    assert result["decision"] in ("buy", "negotiate", "pass") and result["max_bid"] >= 0
    assert (await me(client, shopper))["credit_balance"] == 9
    async with admin_engine.connect() as conn:
        row = (await conn.execute(text(
            "SELECT action, delta, state, subject_id::text FROM credit_transactions WHERE organization_id = :o "
            "AND kind = 'usage'"), {"o": shopper.org_id})).one()
    assert tuple(row) == ("deep_analysis", -1, "settled", analysis["id"])


async def test_deep_analysis_without_credits_is_refused_with_402_before_anything_runs(client, worker, admin_engine):
    _, lot_id = await listed_lot(client, worker)
    shopper = await buyer(client)
    await set_credits(admin_engine, shopper.org_id, 0)
    res = await run_analysis(client, worker, shopper, lot_id)
    assert res.status_code == 402
    error = res.json()["error"]
    assert (error["code"], error["remaining"], error["upgrade_url"]) == ("INSUFFICIENT_CREDITS", 0, "/billing")
    assert (await client.get(f"/marketplace/lots/{lot_id}", headers=shopper.headers)).json()["analyses"] == []


async def test_model_resale_estimates_are_used_only_within_msrp_bounds(client, worker):
    _, lot_id = await listed_lot(client, worker)
    shopper = await buyer(client)

    def answer(request):
        positions = [int(p) for p in __import__("re").findall(r'"position": (\d+)', request.user)]
        values = {1: 60.0, 2: 500.0, 3: 20.0, 4: 70.0}  # 500 is above the item's MSRP: rejected
        return {"items": [{"position": p, "resale_unit": values[p], "velocity": "fast"} for p in positions]}

    set_ai_service(AIService([FakeProvider([answer])]))
    analysis = (await run_analysis(client, worker, shopper, lot_id)).json()
    assert analysis["method"] == "ai"
    sources = {i["position"]: (i["resale_source"], i["resale_unit"]) for i in analysis["result"]["items"]}
    assert sources[1] == ("ai", 60.0) and sources[2][0] == "rules" and sources[4] == ("ai", 70.0)
    assert any("replaced by recovery rates" in n for n in analysis["result"]["notes"])
    assert (await me(client, shopper))["credit_balance"] == 9  # still one credit for the whole run


async def test_analyses_are_private_to_the_buyer(client, worker):
    _, lot_id = await listed_lot(client, worker)
    first = await buyer(client)
    second = await buyer(client, email="other@resell.test", org="Other Resale")
    analysis = (await run_analysis(client, worker, first, lot_id)).json()
    assert (await client.get(f"/marketplace/analyses/{analysis['id']}", headers=second.headers)).status_code == 404
    assert (await client.get(f"/marketplace/lots/{lot_id}", headers=second.headers)).json()["analyses"] == []


async def test_a_requester_can_browse_but_not_spend_credits(client, worker):
    _, lot_id = await listed_lot(client, worker)
    team = await register_team_buyer(client)
    requester = await add_member(client, team, "req@buyteam.test", "requester")
    assert (await client.get(f"/marketplace/lots/{lot_id}", headers=requester.headers)).status_code == 200
    res = await client.post(f"/marketplace/lots/{lot_id}/analyses", json={}, headers=requester.headers)
    assert res.status_code == 403


async def register_team_buyer(client):
    from tests.helpers import register_admin

    return await register_admin(client, email="lead@buyteam.test", org="Buy Team Inc", account_type="org_buyer")


def test_a_model_title_that_changes_a_model_numbers_spelling_is_rejected():
    from app.marketplace.manifest import Line, apply_model

    line = Line(source_line=2, raw={"Description": "DEWALT 20V MAX DRILL DCD771C2"}, title="DEWALT 20V MAX DRILL DCD771C2",
                brand="DeWalt", category="tools", condition="customer_return", upc=None, asin=None, model_number=None,
                quantity=1, unit_msrp=None, unit_price=None)
    answer = {"title": "DeWalt 20V Max Drill Dcd771c2", "brand": None, "category": "tools", "condition": None,
              "upc": None, "asin": None, "model_number": None}
    apply_model(line, answer, (), has_condition_column=False)
    assert line.title == "DEWALT 20V MAX DRILL DCD771C2" and "ai_title_rejected" in line.flags
    apply_model(line, {**answer, "title": "DeWalt 20V Max Drill DCD771C2"}, (), has_condition_column=False)
    assert line.title == "DeWalt 20V Max Drill DCD771C2"
