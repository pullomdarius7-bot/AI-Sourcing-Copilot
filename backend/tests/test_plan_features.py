"""What the pricing page promises: buyer manifest analyses with batch upload, exportable deal sheets, margin rules
and freight overlays, the export API and priority processing."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.ai.service import AIService, set_ai_service
from tests.fakes import FakeProvider
from tests.helpers import register_admin
from tests.market_helpers import MANIFEST, buyer, listed_lot
from tests.test_billing import subscribe


async def me(client, account):
    return (await client.get("/auth/me", headers=account.headers)).json()


async def upload_own(client, worker, account, files=None, price="900.00"):
    files = files or [("files", ("auction-lot.csv", MANIFEST.encode(), "text/csv"))]
    data = {"asking_price": price} if price else {}
    res = await client.post("/buyer/manifests", files=files, data=data, headers=account.headers)
    assert res.status_code == 201, res.text
    await worker.drain()
    return res.json(), (await client.get("/buyer/manifests", headers=account.headers)).json()


async def finished_analysis(client, worker, account, lot_id, **params):
    res = await client.post(f"/marketplace/lots/{lot_id}/analyses", json=params, headers=account.headers)
    assert res.status_code == 202, res.text
    await worker.drain()
    return (await client.get(f"/marketplace/analyses/{res.json()['id']}", headers=account.headers)).json()


# --- Buyer manifest analyses ----------------------------------------------------------------------------------
async def test_a_buyers_own_manifest_is_normalized_and_analysed_automatically(client, worker):
    shopper = await buyer(client)
    _, lots = await upload_own(client, worker, shopper)
    [lot] = lots
    assert lot["ingest_status"] == "ready" and lot["total_units"] == 32
    assert lot["analysis"]["status"] == "succeeded" and lot["analysis"]["result"]["decision"] in ("buy", "negotiate", "pass")
    assert (await me(client, shopper))["credit_balance"] == 10  # rules normalization is free, the analysis is included


async def test_one_full_manifest_analysis_costs_one_credit_and_reruns_cost_one_more(client, worker):
    shopper = await buyer(client)
    set_ai_service(AIService([FakeProvider([{"rows": []}])]))  # the model pass on normalization is charged
    _, lots = await upload_own(client, worker, shopper)
    assert lots[0]["analysis"]["status"] == "succeeded"
    assert (await me(client, shopper))["credit_balance"] == 9  # 1 credit: a full manifest analysis under 500 lines
    detail = (await client.get(f"/marketplace/lots/{lots[0]['id']}", headers=shopper.headers)).json()
    assert detail["own"] is True and detail["analysis_cost"] == 1
    await finished_analysis(client, worker, shopper, lots[0]["id"], channel="amazon")
    assert (await me(client, shopper))["credit_balance"] == 8


async def test_batch_upload_waits_for_each_lots_price_before_analysing(client, worker):
    shopper = await buyer(client)
    files = [("files", (f"lot-{n}.csv", MANIFEST.encode() + f"Extra item {n},,,New,,1,10,10\n".encode(), "text/csv"))
             for n in range(3)]
    _, lots = await upload_own(client, worker, shopper, files=files, price=None)
    assert len(lots) == 3 and all(lot["ingest_status"] == "ready" and lot["analysis"] is None for lot in lots)
    res = await client.patch(f"/buyer/manifests/{lots[0]['id']}", json={"asking_price": "750.00", "title": "Auction 8812"},
                             headers=shopper.headers)
    assert res.status_code == 200
    await worker.drain()
    lots = {lot["id"]: lot for lot in (await client.get("/buyer/manifests", headers=shopper.headers)).json()}
    priced = lots[res.json()["id"]]
    assert priced["title"] == "Auction 8812" and priced["analysis"]["status"] == "succeeded"


async def test_own_manifests_stay_private_and_cannot_be_listed(client, worker, admin_engine):
    shopper = await buyer(client)
    _, [lot] = await upload_own(client, worker, shopper)
    other = await buyer(client, email="nosy@resell.test", org="Nosy Resale")
    assert (await client.get(f"/marketplace/lots/{lot['id']}", headers=other.headers)).status_code == 404
    assert (await client.get("/marketplace/lots", headers=other.headers)).json()["total"] == 0
    with pytest.raises(IntegrityError):  # a private lot can never be published
        async with admin_engine.begin() as conn:
            await conn.execute(text("UPDATE marketplace_lots SET status = 'published' WHERE id = :l"), {"l": lot["id"]})
    res = await client.post(f"/marketplace/lots/{lot['id']}/offers", headers=shopper.headers, json={
        "offered_price": "1", "shipping_terms": "buyer_freight", "target_close_date": "2099-01-01"})
    assert res.status_code == 404


async def test_suppliers_cannot_use_buyer_manifests(client):
    seller = await register_admin(client, email="s@liq.test", org="Liq", account_type="supplier")
    res = await client.post("/buyer/manifests", files=[("files", ("m.csv", MANIFEST.encode(), "text/csv"))],
                            headers=seller.headers)
    assert res.status_code == 403


# --- Deal sheets ------------------------------------------------------------------------------------------------
async def test_analyses_export_as_csv_and_pdf_deal_sheets(client, worker):
    _, lot_id = await listed_lot(client, worker)
    shopper = await buyer(client)
    analysis = await finished_analysis(client, worker, shopper, lot_id)
    csv = await client.get(f"/marketplace/analyses/{analysis['id']}/deal-sheet.csv", headers=shopper.headers)
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=\"deal-sheet-" in csv.headers["content-disposition"]
    text_body = csv.content.decode("utf-8-sig")
    assert "Decision," in text_body and "DEWALT 20V MAX Cordless Drill Kit DCD771C2" in text_body
    pdf = await client.get(f"/marketplace/analyses/{analysis['id']}/deal-sheet.pdf", headers=shopper.headers)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")
    other = await buyer(client, email="nosy@resell.test", org="Nosy Resale")
    assert (await client.get(f"/marketplace/analyses/{analysis['id']}/deal-sheet.pdf", headers=other.headers)).status_code == 404


# --- Margin rules & freight overlays ------------------------------------------------------------------------------
RULES = {"channel": "amazon", "target_margin_pct": 25, "freight_per_pallet": 400, "freight_truckload": 3500,
         "handling_per_unit": 1.5}


async def test_team_margin_rules_and_freight_overlays_drive_every_analysis(client, worker):
    _, lot_id = await listed_lot(client, worker)
    team = await register_admin(client, email="lead@buyteam.test", org="Buy Team Inc")  # team account on trial
    res = await client.put("/analysis-rules", json=RULES, headers=team.headers)
    assert res.status_code == 200 and res.json()["rules"]["freight_per_pallet"] == 400
    analysis = await finished_analysis(client, worker, team, lot_id)
    a = analysis["result"]["assumptions"]
    assert (a["channel"], a["target_margin_pct"], a["freight"], a["handling_per_unit"]) == ("amazon", 25.0, 400.0, 1.5)
    assert analysis["result"]["totals"]["handling"] > 0
    explicit = await finished_analysis(client, worker, team, lot_id, channel="ebay", freight_cost=100)
    assert explicit["result"]["assumptions"]["channel"] == "ebay" and explicit["result"]["assumptions"]["freight"] == 100.0


async def test_margin_rules_are_a_sourcing_team_feature(client):
    solo = await buyer(client)
    assert (await client.get("/analysis-rules", headers=solo.headers)).json()["available"] is False
    res = await client.put("/analysis-rules", json=RULES, headers=solo.headers)
    assert res.status_code == 403 and res.json()["error"]["code"] == "team_plan_required"
    await subscribe(client, solo, plan="enterprise_buyer_monthly")
    assert (await client.put("/analysis-rules", json=RULES, headers=solo.headers)).status_code == 200


# --- Export API ---------------------------------------------------------------------------------------------------
async def test_the_export_api_reads_team_data_with_a_revocable_key(client, worker):
    _, lot_id = await listed_lot(client, worker)
    team = await register_admin(client, email="lead@buyteam.test", org="Buy Team Inc")
    await finished_analysis(client, worker, team, lot_id)
    created = await client.post("/api-keys", json={"name": "BI dashboard"}, headers=team.headers)
    assert created.status_code == 201
    key = created.json()["key"]
    assert key.startswith("sca_") and created.json()["prefix"] == key[:12]
    listed = (await client.get("/api-keys", headers=team.headers)).json()["keys"]
    assert "key" not in listed[0]

    api = {"Authorization": f"Bearer {key}"}
    analyses = await client.get("/export/analyses", headers=api)
    assert analyses.status_code == 200 and len(analyses.json()["data"]) == 1
    assert (await client.get("/export/sourcing-requests", headers=api)).status_code == 200
    assert (await client.get("/export/manifests", headers=api)).status_code == 200
    assert (await client.get("/api-keys", headers=team.headers)).json()["keys"][0]["last_used_at"] is not None

    other = await register_admin(client, email="other@team.test", org="Other Team")
    other_key = (await client.post("/api-keys", json={"name": "x"}, headers=other.headers)).json()["key"]
    assert (await client.get("/export/analyses", headers={"Authorization": f"Bearer {other_key}"})).json()["data"] == []

    await client.delete(f"/api-keys/{created.json()['id']}", headers=team.headers)
    assert (await client.get("/export/analyses", headers=api)).status_code == 401
    assert (await client.get("/export/analyses", headers={"Authorization": "Bearer sca_made_up"})).status_code == 401


async def test_the_export_api_is_a_sourcing_team_feature(client):
    solo = await buyer(client)
    res = await client.post("/api-keys", json={"name": "x"}, headers=solo.headers)
    assert res.status_code == 403 and res.json()["error"]["code"] == "team_plan_required"


# --- Priority processing --------------------------------------------------------------------------------------------
async def test_sourcing_team_jobs_are_processed_first(client, worker, admin_engine):
    _, lot_id = await listed_lot(client, worker)
    solo = await buyer(client)
    team = await buyer(client, email="team@resell.test", org="Team Resale")
    await subscribe(client, team, plan="enterprise_buyer_monthly", customer="cus_t", subscription="sub_t", invoice="in_t")
    first = await client.post(f"/marketplace/lots/{lot_id}/analyses", json={}, headers=solo.headers)
    second = await client.post(f"/marketplace/lots/{lot_id}/analyses", json={}, headers=team.headers)
    async with admin_engine.connect() as conn:
        rows = dict((await conn.execute(text(
            "SELECT payload->>'analysis_id', priority FROM jobs WHERE kind = 'marketplace.analyze'"))).all())
    assert rows == {first.json()["id"]: 0, second.json()["id"]: 10}
    await worker.run_once()
    team_analysis = (await client.get(f"/marketplace/analyses/{second.json()['id']}", headers=team.headers)).json()
    solo_analysis = (await client.get(f"/marketplace/analyses/{first.json()['id']}", headers=solo.headers)).json()
    assert (team_analysis["status"], solo_analysis["status"]) == ("succeeded", "queued")


async def test_billing_shows_support_and_team_features(client, monkeypatch):
    from app.routers import billing

    monkeypatch.setattr(billing.settings, "support_email", "help@sourcing.example")
    team = await register_admin(client)
    await subscribe(client, team, plan="enterprise_buyer_monthly")
    body = (await client.get("/billing", headers=team.headers)).json()
    assert body["team_features"] is True and body["support"] == {"email": "help@sourcing.example", "priority": True}
