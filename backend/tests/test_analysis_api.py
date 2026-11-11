"""Sprint 5 over the API: reference data, quote sheet import, analysis inputs, award recommendation and approval."""
import io
from datetime import date, timedelta

import pytest
from openpyxl import Workbook
from pypdf import PdfReader

from tests.rfq_helpers import line_for, org, qualified_supplier, sent_rfq, verified_quote
from tests.test_quotes_comparison import three_way

TODAY = date.today()


async def compare(client, o, rfq, **body):
    res = await client.post(f"/rfqs/{rfq['id']}/comparisons", json=body, headers=o.buyer.headers)
    assert res.status_code == 201, res.text
    return res.json()


async def recommend(client, o, rfq, cmp_, supplier, rationale="Best balance of price, lead time and risk", who=None):
    return await client.post(f"/rfqs/{rfq['id']}/recommendations", json={
        "comparison_id": cmp_["id"], "supplier_id": supplier["id"], "rationale": rationale,
    }, headers=(who or o.buyer).headers)


# --- Reference data ---------------------------------------------------------------------------------------------
async def test_fx_rates_crud_permissions_and_audit(client):
    o = await org(client)
    body = {"base": "eur", "quote": "usd", "rate": "1.0850", "valid_from": TODAY.isoformat(), "source": "Treasury memo 12"}
    assert (await client.post("/fx-rates", json=body, headers=o.buyer.headers)).status_code == 403
    res = await client.post("/fx-rates", json=body, headers=o.approver.headers)
    assert res.status_code == 201, res.text
    rate = res.json()
    assert rate["base"] == "EUR" and rate["quote"] == "USD" and float(rate["rate"]) == 1.085
    dup = await client.post("/fx-rates", json=body, headers=o.approver.headers)
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "rate_exists"
    bad = await client.post("/fx-rates", json={**body, "quote": "EUR"}, headers=o.approver.headers)
    assert bad.status_code == 400
    assert (await client.post("/fx-rates", json={**body, "rate": "0"}, headers=o.approver.headers)).status_code == 422
    listed = (await client.get("/fx-rates?currency=USD", headers=o.auditor.headers)).json()
    assert [r["id"] for r in listed] == [rate["id"]]
    assert (await client.delete(f"/fx-rates/{rate['id']}", headers=o.approver.headers)).status_code == 204
    actions = {r["action"] for r in (await client.get("/audit-logs", headers=o.admin.headers)).json()}
    assert {"fx_rate.created", "fx_rate.deleted"} <= actions


async def test_unit_rules_and_catalogue(client):
    o = await org(client)
    res = await client.post("/unit-conversions", json={"from_unit": "Box", "to_unit": "pieces", "factor": "50", "note": "Carton of 50"},
                            headers=o.admin.headers)
    assert res.status_code == 201, res.text
    assert res.json()["from_unit"] == "box" and res.json()["to_unit"] == "pcs"
    inverse = await client.post("/unit-conversions", json={"from_unit": "pcs", "to_unit": "box", "factor": "0.02"}, headers=o.admin.headers)
    assert inverse.status_code == 409
    standard = await client.post("/unit-conversions", json={"from_unit": "kg", "to_unit": "lb", "factor": "2.2"}, headers=o.admin.headers)
    assert standard.status_code == 400 and standard.json()["error"]["code"] == "standard_conversion"
    check = (await client.get("/unit-conversions/check?from_unit=box&to_unit=dozen", headers=o.buyer.headers)).json()
    assert check["custom"] is True and check["factor"].startswith("4.1666")
    none = (await client.get("/unit-conversions/check?from_unit=kg&to_unit=pcs", headers=o.buyer.headers)).json()
    assert none["factor"] is None
    catalogue = (await client.get("/units", headers=o.requester.headers)).json()
    assert {"unit": "lb", "dimension": "mass", "to_base": "0.45359237"} == {k: v for k, v in next(u for u in catalogue if u["unit"] == "lb").items() if k != "aliases"}


async def test_reference_data_is_isolated(client):
    a = await org(client)
    b = await org(client, email="admin@beta.test", name="Beta", domain="beta.test")
    rate = (await client.post("/fx-rates", json={"base": "EUR", "quote": "USD", "rate": "1.1", "valid_from": TODAY.isoformat()},
                              headers=a.admin.headers)).json()
    assert (await client.get("/fx-rates", headers=b.admin.headers)).json() == []
    assert (await client.delete(f"/fx-rates/{rate['id']}", headers=b.admin.headers)).status_code == 404


# --- Analysis inputs over the API --------------------------------------------------------------------------------
async def test_foreign_currency_quote_is_converted_once_a_rate_exists(client, worker):
    o = await org(client)
    a = await qualified_supplier(client, o, "Northwind Metals")
    b = await qualified_supplier(client, o, "Liberty Supply")
    rfq = await sent_rfq(client, worker, o, [a, b])
    await verified_quote(client, o, rfq, a, [line_for(rfq, 0, unit_price=4, quantity=5000, unit="pcs", lead_time_days=10)])
    await verified_quote(client, o, rfq, b, [line_for(rfq, 0, unit_price=4.4, quantity=5000, unit="pcs", lead_time_days=10)], currency="USD")
    first = await compare(client, o, rfq)
    assert {e["reason"] for e in first["result"]["excluded"]} == {"no_fx_rate"}
    await client.post("/fx-rates", json={"base": "EUR", "quote": "USD", "rate": "1.1", "valid_from": TODAY.isoformat()}, headers=o.approver.headers)
    assert (await client.get(f"/comparisons/{first['id']}", headers=o.buyer.headers)).json()["stale"] is True  # a new rate changes the inputs
    result = (await compare(client, o, rfq))["result"]
    liberty = next(r for r in result["ranking"] if r["supplier_name"] == "Liberty Supply")
    assert liberty["basket_total"] == pytest.approx(20000.0) and result["fx_used"]["USD"]["rate"] == pytest.approx(1 / 1.1)
    assert "converted" in " ".join(result["global_flags"]).lower()


async def test_moq_lead_time_and_requested_terms(client, worker):
    o = await org(client)
    a = await qualified_supplier(client, o, "Northwind Metals")
    b = await qualified_supplier(client, o, "Kessler GmbH")
    required = (TODAY + timedelta(days=20)).isoformat()
    rfq = await sent_rfq(client, worker, o, [a, b], required_by=required, requested_incoterms="dap", requested_payment_terms="Net 30")
    assert rfq["required_by"] == required and rfq["requested_incoterms"] == "DAP"
    await verified_quote(client, o, rfq, a, [line_for(rfq, 0, unit_price=4, quantity=5000, unit="pcs", lead_time_days=10, moq=10000)])
    await verified_quote(client, o, rfq, b, [line_for(rfq, 0, unit_price=4.2, quantity=5000, unit="pcs", lead_time_days=30)],
                         incoterms="EXW", payment_terms="Prepayment")
    result = (await compare(client, o, rfq))["result"]
    north = next(r for r in result["ranking"] if r["supplier_name"] == "Northwind Metals")
    kessler = next(r for r in result["ranking"] if r["supplier_name"] == "Kessler GmbH")
    assert north["moq_excess_total"] == pytest.approx(20000.0) and north["basket_total"] == pytest.approx(40000.0)
    assert north["on_time"] is True and kessler["on_time"] is False
    assert {"incoterms_differ", "payment_terms_differ", "lead_time_exceeds_required"} <= set(kessler["flags"])
    assert result["lead_time_analysis"]["late_suppliers"] == ["Kessler GmbH"]
    quotes = (await client.get(f"/rfqs/{rfq['id']}/quotes", headers=o.buyer.headers)).json()
    assert next(q for q in quotes if q["supplier_name"] == "Northwind Metals")["lines"][0]["moq"] == 10000


# --- Quote sheet import ----------------------------------------------------------------------------------------------
def sheet() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(["Quotation Q-4711"])
    ws.append(["RFQ line", "Description", "Qty", "Unit", "Unit price (EUR)", "Lead time", "MOQ"])
    ws.append([1, "Bracket AISI 304 3mm", 5000, "pcs", 4.1, 21, 2000])
    ws.append([2, "Brushed finish", 5000, "pcs", "on request", 21, None])
    ws.append([None, "Freight", None, None, 150, None, None])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


async def test_quote_import_creates_a_draft_with_suggested_matches(client, worker):
    o = await org(client)
    a = await qualified_supplier(client, o, "Northwind Metals")
    rfq = await sent_rfq(client, worker, o, [a])
    res = await client.post(f"/rfqs/{rfq['id']}/quotes/import", data={"supplier_id": a["id"], "incoterms": "dap", "valid_until": (TODAY + timedelta(days=60)).isoformat()},
                            files={"file": ("q4711.xlsx", sheet(), XLSX)}, headers=o.buyer.headers)
    assert res.status_code == 201, res.text
    body = res.json()
    quote = body["quote"]
    assert quote["source"] == "import" and quote["status"] == "draft" and quote["currency"] == "EUR" and quote["incoterms"] == "DAP"
    assert body["currency_detected"] == "EUR" and any("on request" in w for w in body["warnings"])
    first, second, freight = quote["lines"]
    assert first["match_status"] == "suggested" and first["rfq_item_id"] == rfq["items"][0]["id"] and first["moq"] == 2000
    assert second["unit_price"] is None and "row 4" in second["source_quote"]
    assert freight["rfq_item_id"] is None or freight["match_status"] == "suggested"
    # nothing is compared until a person confirms the matches and verifies the quote
    verify = await client.post(f"/quotes/{quote['id']}/verify", headers=o.buyer.headers)
    assert verify.status_code == 409 and verify.json()["error"]["code"] == "quote_incomplete"


@pytest.mark.parametrize("name,data,status,code", [
    ("q.csv", b"foo,bar\n1,2\n", 422, "unreadable_sheet"),
    ("q.pdf", b"%PDF-1.4", 422, "unreadable_sheet"),
    ("q.csv", b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*", 422, "file_rejected"),
])
async def test_quote_import_rejects_bad_files(client, worker, name, data, status, code):
    o = await org(client)
    a = await qualified_supplier(client, o, "Northwind Metals")
    rfq = await sent_rfq(client, worker, o, [a])
    res = await client.post(f"/rfqs/{rfq['id']}/quotes/import", data={"supplier_id": a["id"]},
                            files={"file": (name, data, "text/csv")}, headers=o.buyer.headers)
    assert res.status_code == status and res.json()["error"]["code"] == code


async def test_quote_import_permissions(client, worker):
    o = await org(client)
    a = await qualified_supplier(client, o, "Northwind Metals")
    rfq = await sent_rfq(client, worker, o, [a])
    for who in (o.requester, o.auditor, o.approver):
        res = await client.post(f"/rfqs/{rfq['id']}/quotes/import", data={"supplier_id": a["id"]},
                                files={"file": ("q.xlsx", sheet(), XLSX)}, headers=who.headers)
        assert res.status_code == 403


# --- Award recommendation & approval ------------------------------------------------------------------------------------
async def test_award_needs_recommendation_then_separate_approval(client, worker):
    o, rfq, (a, b, c), (qa, _, _) = await three_way(client, worker)
    cmp_ = await compare(client, o, rfq)
    assert (await recommend(client, o, rfq, cmp_, a, rationale="cheap")).status_code == 422
    res = await recommend(client, o, rfq, cmp_, a, rationale="Existing tooling, proven quality on PO 7781")
    assert res.status_code == 201, res.text
    rec = res.json()
    assert rec["status"] == "pending" and rec["not_top_ranked"] is True and rec["snapshot"]["rank"] == 2
    assert (await client.get(f"/rfqs/{rfq['id']}", headers=o.buyer.headers)).json()["status"] == "sent"  # nothing awarded yet
    assert (await recommend(client, o, rfq, cmp_, c)).json()["error"]["code"] == "recommendation_pending"
    # the buyer cannot approve; the approver must give a reason for picking a lower-ranked supplier
    assert (await client.post(f"/recommendations/{rec['id']}/approve", json={}, headers=o.buyer.headers)).status_code == 403
    res = await client.post(f"/recommendations/{rec['id']}/approve", json={}, headers=o.approver.headers)
    assert res.status_code == 400 and res.json()["error"]["code"] == "note_required"
    res = await client.post(f"/recommendations/{rec['id']}/approve", json={"note": "Agreed: tooling cost outweighs the gap"}, headers=o.approver.headers)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "approved" and res.json()["decided_by"] == o.approver.user_id
    detail = (await client.get(f"/rfqs/{rfq['id']}", headers=o.buyer.headers)).json()
    assert detail["status"] == "awarded" and detail["awarded_supplier_id"] == a["id"] and detail["awarded_by"] == o.approver.user_id
    assert detail["award_recommendation_id"] == rec["id"] and detail["award_note"].startswith("Existing tooling")
    award = next(e for e in (await client.get(f"/rfqs/{rfq['id']}/events", headers=o.auditor.headers)).json() if e["action"] == "awarded")
    assert award["data"]["deviates_from_ranking"] is True and award["data"]["recommended_by"] == o.buyer.user_id
    assert (await client.post(f"/quotes/{qa['id']}/reopen", json={"reason": "late change"}, headers=o.buyer.headers)).status_code == 409
    text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO((await client.get(f"/comparisons/{cmp_['id']}/pdf", headers=o.buyer.headers)).content)).pages)
    assert "Awarded to Northwind Metals" in text and "Risk" in text


async def test_reject_and_withdraw(client, worker):
    o, rfq, (a, b, c), _ = await three_way(client, worker)
    cmp_ = await compare(client, o, rfq)
    rec = (await recommend(client, o, rfq, cmp_, c)).json()
    assert (await client.post(f"/recommendations/{rec['id']}/reject", json={}, headers=o.approver.headers)).status_code == 400
    res = await client.post(f"/recommendations/{rec['id']}/reject", json={"note": "Check FOB freight first"}, headers=o.approver.headers)
    assert res.status_code == 200 and res.json()["status"] == "rejected"
    rec2 = (await recommend(client, o, rfq, cmp_, c)).json()
    assert (await client.post(f"/recommendations/{rec2['id']}/withdraw", json={}, headers=o.approver.headers)).status_code == 403
    res = await client.post(f"/recommendations/{rec2['id']}/withdraw", json={"note": "Re-running"}, headers=o.buyer.headers)
    assert res.json()["status"] == "withdrawn"
    history = (await client.get(f"/rfqs/{rfq['id']}/recommendations", headers=o.auditor.headers)).json()
    assert [h["status"] for h in history] == ["withdrawn", "rejected"]


async def test_recommendation_requires_current_comparison_and_qualified_supplier(client, worker):
    o, rfq, (a, b, c), (qa, _, _) = await three_way(client, worker)
    outsider = await qualified_supplier(client, o, "Outsider Ltd")
    cmp_ = await compare(client, o, rfq)
    assert (await recommend(client, o, rfq, cmp_, outsider)).json()["error"]["code"] == "not_ranked"
    rec = (await recommend(client, o, rfq, cmp_, c)).json()
    await client.post(f"/quotes/{qa['id']}/reopen", json={"reason": "Supplier revised price"}, headers=o.buyer.headers)
    listed = (await client.get(f"/rfqs/{rfq['id']}/recommendations", headers=o.approver.headers)).json()
    assert listed[0]["comparison_stale"] is True
    res = await client.post(f"/recommendations/{rec['id']}/approve", json={"note": "ok"}, headers=o.approver.headers)
    assert res.status_code == 409 and res.json()["error"]["code"] == "stale_comparison"
    await client.post(f"/recommendations/{rec['id']}/withdraw", json={}, headers=o.buyer.headers)
    assert (await recommend(client, o, rfq, cmp_, c)).json()["error"]["code"] == "stale_comparison"
    await client.post(f"/quotes/{qa['id']}/verify", headers=o.buyer.headers)
    cmp2 = await compare(client, o, rfq)
    rec = (await recommend(client, o, rfq, cmp2, c)).json()
    await client.post(f"/suppliers/{c['id']}/block", json={"reason": "Sanctions hit"}, headers=o.approver.headers)
    res = await client.post(f"/recommendations/{rec['id']}/approve", json={"note": "ok"}, headers=o.approver.headers)
    assert res.status_code == 409  # blocking changes the qualification input, so the comparison is stale too
    assert res.json()["error"]["code"] in ("stale_comparison", "supplier_not_qualified")


async def test_cancel_withdraws_pending_recommendation(client, worker):
    o, rfq, (a, b, c), _ = await three_way(client, worker)
    rec = (await recommend(client, o, rfq, await compare(client, o, rfq), c)).json()
    assert (await client.post(f"/rfqs/{rfq['id']}/cancel", json={"reason": "Project stopped"}, headers=o.buyer.headers)).status_code == 200
    history = (await client.get(f"/rfqs/{rfq['id']}/recommendations", headers=o.buyer.headers)).json()
    assert history[0]["id"] == rec["id"] and history[0]["status"] == "withdrawn"


async def test_recommendations_are_isolated_between_organizations(client, worker):
    o, rfq, (a, b, c), _ = await three_way(client, worker)
    rec = (await recommend(client, o, rfq, await compare(client, o, rfq), c)).json()
    other = await org(client, email="admin@beta.test", name="Beta", domain="beta.test")
    for path in (f"/recommendations/{rec['id']}/approve", f"/recommendations/{rec['id']}/reject", f"/recommendations/{rec['id']}/withdraw"):
        assert (await client.post(path, json={"note": "taking over"}, headers=other.admin.headers)).status_code == 404
    assert (await client.get(f"/rfqs/{rfq['id']}/recommendations", headers=other.admin.headers)).status_code == 404
