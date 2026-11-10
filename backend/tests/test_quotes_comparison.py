import io

import pytest
from openpyxl import load_workbook
from pypdf import PdfReader

from app.ai.service import AIService, set_ai_service
from tests.fakes import FakeProvider, good_analysis
from tests.rfq_helpers import (
    DUE,
    approved_rfq,
    line_for,
    org,
    qualified_supplier,
    sent_rfq,
    verified_quote,
)
from tests.samples import QUOTE


async def three_way(client, worker):
    o = await org(client)
    a = await qualified_supplier(client, o, "Northwind Metals")
    b = await qualified_supplier(client, o, "Kessler GmbH")
    c = await qualified_supplier(client, o, "Orion Cast")
    rfq = await sent_rfq(client, worker, o, [a, b, c])
    qa = await verified_quote(client, o, rfq, a, [line_for(rfq, 0, unit_price=4.20, quantity=5000, unit="pcs", lead_time_days=21),
                                                  line_for(rfq, 1, unit_price=0.30, quantity=5000, unit="pcs", lead_time_days=21)])
    qb = await verified_quote(client, o, rfq, b, [line_for(rfq, 0, unit_price=4.00, quantity=5000, unit="pcs", lead_time_days=35),
                                                  line_for(rfq, 1, unit_price=0.40, quantity=5000, unit="pcs", lead_time_days=35)])
    qc = await verified_quote(client, o, rfq, c, [line_for(rfq, 0, unit_price=3.90, quantity=5000, unit="pcs", lead_time_days=14),
                                                  line_for(rfq, 1, unit_price=0.25, quantity=5000, unit="pcs", lead_time_days=14)],
                              incoterms="FOB")
    return o, rfq, (a, b, c), (qa, qb, qc)


# --- Quotes ----------------------------------------------------------------------------------------------------
async def test_quotes_need_a_sent_rfq_and_an_invited_supplier(client, worker):
    o = await org(client)
    a = await qualified_supplier(client, o, "Northwind Metals")
    outsider = await qualified_supplier(client, o, "Outsider Ltd")
    approved = await approved_rfq(client, o, [a])
    res = await client.post(f"/rfqs/{approved['id']}/quotes", json={"supplier_id": a["id"]}, headers=o.buyer.headers)
    assert res.status_code == 409 and res.json()["error"]["code"] == "rfq_not_open"
    rfq = await sent_rfq(client, worker, o, [a])
    res = await client.post(f"/rfqs/{rfq['id']}/quotes", json={"supplier_id": outsider["id"]}, headers=o.buyer.headers)
    assert res.status_code == 409 and res.json()["error"]["code"] == "not_invited"
    first = await client.post(f"/rfqs/{rfq['id']}/quotes", json={"supplier_id": a["id"], "currency": "EUR"}, headers=o.buyer.headers)
    dup = await client.post(f"/rfqs/{rfq['id']}/quotes", json={"supplier_id": a["id"]}, headers=o.buyer.headers)
    assert first.status_code == 201 and dup.status_code == 409 and dup.json()["error"]["code"] == "quote_exists"
    detail = (await client.get(f"/rfqs/{rfq['id']}", headers=o.buyer.headers)).json()
    assert detail["suppliers"][0]["status"] == "quoted"


async def test_line_matching_is_suggested_then_confirmed_by_a_person(client, worker):
    o = await org(client)
    a = await qualified_supplier(client, o, "Northwind Metals")
    rfq = await sent_rfq(client, worker, o, [a])
    body = {"supplier_id": a["id"], "currency": "EUR", "lines": [
        {"description": "Bracket stainless AISI 304, 3mm", "unit_price": 4.2, "quantity": 5000, "unit": "pcs"},
        {"description": "Brushed finish surcharge", "unit_price": 0.3, "quantity": 5000, "unit": "pcs"},
        {"description": "Freight to Rotterdam", "total_price": 350},
    ]}
    quote = (await client.post(f"/rfqs/{rfq['id']}/quotes", json=body, headers=o.buyer.headers)).json()
    lines = quote["lines"]
    assert [ln["match_status"] for ln in lines] == ["suggested", "suggested", "unmatched"]
    assert lines[0]["rfq_item_id"] == rfq["items"][0]["id"] and lines[1]["rfq_item_id"] == rfq["items"][1]["id"]
    res = await client.post(f"/quotes/{quote['id']}/verify", headers=o.buyer.headers)
    assert res.status_code == 409 and "Confirm or clear every suggested line match" in res.json()["error"]["message"]
    for ln in lines[:2]:
        res = await client.post(f"/quotes/{quote['id']}/lines/{ln['id']}/match", json={"rfq_item_id": ln["rfq_item_id"]}, headers=o.buyer.headers)
        assert res.status_code == 200
    verified = await client.post(f"/quotes/{quote['id']}/verify", headers=o.buyer.headers)
    assert verified.status_code == 200 and verified.json()["status"] == "verified" and verified.json()["verified_by"] == o.buyer.user_id
    assert [ln["match_status"] for ln in verified.json()["lines"]] == ["confirmed", "confirmed", "unmatched"]
    locked = await client.patch(f"/quotes/{quote['id']}", json={"payment_terms": "Net 90"}, headers=o.buyer.headers)
    assert locked.status_code == 409 and locked.json()["error"]["code"] == "quote_verified"
    reopened = await client.post(f"/quotes/{quote['id']}/reopen", json={"reason": "Supplier revised price"}, headers=o.buyer.headers)
    assert reopened.json()["status"] == "draft" and reopened.json()["verified_by"] is None


async def test_quote_validation(client, worker):
    o = await org(client)
    a = await qualified_supplier(client, o, "Northwind Metals")
    rfq = await sent_rfq(client, worker, o, [a])
    url = f"/rfqs/{rfq['id']}/quotes"
    for bad in ({"supplier_id": a["id"], "currency": "EURO"}, {"supplier_id": a["id"], "lines": [{"description": "x", "unit_price": -1}]},
                {"supplier_id": a["id"], "lines": [{"description": "x", "rfq_item_id": a["id"]}]}):
        res = await client.post(url, json=bad, headers=o.buyer.headers)
        assert res.status_code in (400, 422), bad
    empty = (await client.post(url, json={"supplier_id": a["id"]}, headers=o.buyer.headers)).json()
    res = await client.post(f"/quotes/{empty['id']}/verify", headers=o.buyer.headers)
    msg = res.json()["error"]["message"]
    assert res.status_code == 409 and "no lines" in msg and "currency" in msg


async def test_quote_from_an_analysed_document_keeps_unknowns_unknown(client, worker):
    o = await org(client)
    a = await qualified_supplier(client, o, "Northwind Metals")
    rfq = await sent_rfq(client, worker, o, [a])
    set_ai_service(AIService([FakeProvider([good_analysis()])]))
    doc = (await client.post("/documents", files={"file": ("quote.txt", QUOTE.encode())}, headers=o.buyer.headers)).json()["document"]
    await worker.drain()
    res = await client.post(f"/rfqs/{rfq['id']}/quotes/from-document", json={"supplier_id": a["id"], "document_id": doc["id"]}, headers=o.buyer.headers)
    assert res.status_code == 201, res.text
    quote = res.json()
    assert quote["source"] == "analysis" and quote["document_id"] == doc["id"] and quote["status"] == "draft"
    assert (quote["currency"], quote["incoterms"], quote["payment_terms"], quote["valid_until"]) == ("EUR", "FOB", "Net 30", "2026-12-31")
    line = quote["lines"][0]
    assert (line["quantity"], line["unit_price"], line["total_price"], line["lead_time_days"]) == (100, 4.2, 420, 21)
    assert line["unit"] is None  # the analysis did not state a unit; nothing is assumed
    assert line["source_quote"].startswith("Item: Steel bracket M8") and line["confidence"] == 0.9
    assert line["match_status"] == "suggested"


async def test_quote_from_document_needs_an_analysis(client, worker):
    o = await org(client)
    a = await qualified_supplier(client, o, "Northwind Metals")
    rfq = await sent_rfq(client, worker, o, [a])
    doc = (await client.post("/documents", files={"file": ("quote.txt", QUOTE.encode())}, headers=o.buyer.headers)).json()["document"]
    await worker.drain()  # no AI provider: analysis is skipped
    res = await client.post(f"/rfqs/{rfq['id']}/quotes/from-document", json={"supplier_id": a["id"], "document_id": doc["id"]}, headers=o.buyer.headers)
    assert res.status_code == 409 and res.json()["error"]["code"] == "no_analysis"


# --- Comparison --------------------------------------------------------------------------------------------------
async def test_comparison_ranks_verified_quotes_and_is_stored(client, worker):
    o, rfq, (a, b, c), _ = await three_way(client, worker)
    res = await client.post(f"/rfqs/{rfq['id']}/comparisons", json={}, headers=o.approver.headers)
    assert res.status_code == 201, res.text
    cmp_ = res.json()
    result = cmp_["result"]
    assert cmp_["stale"] is False and result["decision"] == "human"
    assert [r["supplier_name"] for r in result["ranking"]] == ["Orion Cast", "Northwind Metals", "Kessler GmbH"]
    assert result["highest_score_supplier_id"] == c["id"] and result["ranking"][0]["score"] == 100
    totals = {r["supplier_name"]: r["basket_total"] for r in result["ranking"]}
    assert totals == {"Orion Cast": pytest.approx(20750), "Northwind Metals": pytest.approx(22500), "Kessler GmbH": pytest.approx(22000)}
    assert any("Incoterms differ" in f for f in result["global_flags"])
    listing = (await client.get(f"/rfqs/{rfq['id']}/comparisons", headers=o.auditor.headers)).json()
    assert listing[0]["id"] == cmp_["id"] and listing[0]["stale"] is False


async def test_custom_weights_and_validation(client, worker):
    o, rfq, (a, b, c), _ = await three_way(client, worker)
    lead_heavy = (await client.post(f"/rfqs/{rfq['id']}/comparisons", json={"weights": {"lead_time": 1}}, headers=o.buyer.headers)).json()
    assert lead_heavy["result"]["weights"] == {"price": 0.0, "lead_time": 1.0, "coverage": 0.0, "qualification": 0.0}
    assert lead_heavy["result"]["ranking"][-1]["supplier_name"] == "Kessler GmbH"
    bad = await client.post(f"/rfqs/{rfq['id']}/comparisons", json={"weights": {"price": -1}}, headers=o.buyer.headers)
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_weights"


async def test_draft_quotes_are_excluded_with_a_reason(client, worker):
    o = await org(client)
    a = await qualified_supplier(client, o, "Northwind Metals")
    b = await qualified_supplier(client, o, "Kessler GmbH")
    rfq = await sent_rfq(client, worker, o, [a, b])
    await verified_quote(client, o, rfq, a, [line_for(rfq, 0, unit_price=4, quantity=5000, unit="pcs", lead_time_days=10)])
    await client.post(f"/rfqs/{rfq['id']}/quotes", json={"supplier_id": b["id"], "currency": "EUR"}, headers=o.buyer.headers)
    result = (await client.post(f"/rfqs/{rfq['id']}/comparisons", json={}, headers=o.buyer.headers)).json()["result"]
    assert [r["supplier_name"] for r in result["ranking"]] == ["Northwind Metals"]
    assert result["excluded"] == [{"supplier_id": b["id"], "supplier_name": "Kessler GmbH", "reason": "not_verified",
                                   "detail": "Quote has not been verified by a person"}]


async def test_comparison_needs_a_sent_rfq(client):
    o = await org(client)
    a = await qualified_supplier(client, o, "Northwind Metals")
    rfq = await approved_rfq(client, o, [a])
    res = await client.post(f"/rfqs/{rfq['id']}/comparisons", json={}, headers=o.buyer.headers)
    assert res.status_code == 409


async def test_comparison_exports(client, worker):
    o, rfq, _, _ = await three_way(client, worker)
    cmp_ = (await client.post(f"/rfqs/{rfq['id']}/comparisons", json={}, headers=o.buyer.headers)).json()
    pdf = await client.get(f"/comparisons/{cmp_['id']}/pdf", headers=o.auditor.headers)
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf.content)).pages)
    assert "Supplier comparison" in text and "Orion Cast" in text and "decision support only" in text
    xl = await client.get(f"/comparisons/{cmp_['id']}/xlsx", headers=o.auditor.headers)
    wb = load_workbook(io.BytesIO(xl.content))
    assert wb["Ranking"]["B2"].value == "Orion Cast" and wb["About"]["B3"].value == rfq["title"]
    actions = [r["meta"]["format"] for r in (await client.get("/audit-logs?action=comparison.exported", headers=o.admin.headers)).json()]
    assert sorted(actions) == ["pdf", "xlsx"]


# --- Award ----------------------------------------------------------------------------------------------------------
async def test_award_is_a_recorded_human_decision(client, worker):
    o, rfq, (a, b, c), (qa, _, _) = await three_way(client, worker)
    cmp_ = (await client.post(f"/rfqs/{rfq['id']}/comparisons", json={}, headers=o.buyer.headers)).json()
    short = await client.post(f"/rfqs/{rfq['id']}/award", json={"supplier_id": a["id"], "comparison_id": cmp_["id"], "note": "cheap"}, headers=o.approver.headers)
    assert short.status_code == 422
    buyer = await client.post(f"/rfqs/{rfq['id']}/award", json={"supplier_id": a["id"], "comparison_id": cmp_["id"], "note": "Existing tooling, proven quality"}, headers=o.buyer.headers)
    assert buyer.status_code == 403
    res = await client.post(f"/rfqs/{rfq['id']}/award", json={"supplier_id": a["id"], "comparison_id": cmp_["id"],
                                                             "note": "Existing tooling, proven quality on PO 7781"}, headers=o.approver.headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "awarded" and body["awarded_supplier_id"] == a["id"] and body["awarded_by"] == o.approver.user_id
    assert body["award_comparison_id"] == cmp_["id"] and body["award_note"].startswith("Existing tooling")
    award = next(e for e in (await client.get(f"/rfqs/{rfq['id']}/events", headers=o.auditor.headers)).json() if e["action"] == "awarded")
    assert award["data"]["deviates_from_ranking"] is True and award["data"]["rank"] == 2  # Orion Cast scored highest
    assert (await client.post(f"/quotes/{qa['id']}/reopen", json={"reason": "late change"}, headers=o.buyer.headers)).status_code == 409
    assert (await client.post(f"/rfqs/{rfq['id']}/cancel", json={"reason": "too late now"}, headers=o.buyer.headers)).status_code == 409
    text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO((await client.get(f"/comparisons/{cmp_['id']}/pdf", headers=o.buyer.headers)).content)).pages)
    assert "Awarded to Northwind Metals" in text


async def test_award_requires_a_current_comparison(client, worker):
    o, rfq, (a, b, c), (qa, _, _) = await three_way(client, worker)
    cmp_ = (await client.post(f"/rfqs/{rfq['id']}/comparisons", json={}, headers=o.buyer.headers)).json()
    await client.post(f"/quotes/{qa['id']}/reopen", json={"reason": "Supplier revised price"}, headers=o.buyer.headers)
    assert (await client.get(f"/comparisons/{cmp_['id']}", headers=o.buyer.headers)).json()["stale"] is True
    res = await client.post(f"/rfqs/{rfq['id']}/award", json={"supplier_id": c["id"], "comparison_id": cmp_["id"], "note": "Lowest price and fastest delivery"}, headers=o.approver.headers)
    assert res.status_code == 409 and res.json()["error"]["code"] == "stale_comparison"


async def test_award_only_to_a_ranked_qualified_supplier(client, worker):
    o, rfq, (a, b, c), _ = await three_way(client, worker)
    outsider = await qualified_supplier(client, o, "Outsider Ltd")
    cmp_ = (await client.post(f"/rfqs/{rfq['id']}/comparisons", json={}, headers=o.buyer.headers)).json()
    res = await client.post(f"/rfqs/{rfq['id']}/award", json={"supplier_id": outsider["id"], "comparison_id": cmp_["id"], "note": "Personal preference here"}, headers=o.approver.headers)
    assert res.status_code == 409 and res.json()["error"]["code"] == "not_ranked"
    await client.post(f"/suppliers/{c['id']}/block", json={"reason": "Sanctions hit"}, headers=o.approver.headers)
    cmp_ = (await client.post(f"/rfqs/{rfq['id']}/comparisons", json={}, headers=o.buyer.headers)).json()
    res = await client.post(f"/rfqs/{rfq['id']}/award", json={"supplier_id": c["id"], "comparison_id": cmp_["id"], "note": "Lowest price and fastest delivery"}, headers=o.approver.headers)
    assert res.status_code == 409 and res.json()["error"]["code"] == "supplier_not_qualified"


async def test_quotes_and_comparisons_are_isolated_between_organizations(client, worker):
    o, rfq, _, (qa, _, _) = await three_way(client, worker)
    b = await org(client, email="admin@beta.test", name="Beta", domain="beta.test")
    cmp_ = (await client.post(f"/rfqs/{rfq['id']}/comparisons", json={}, headers=o.buyer.headers)).json()
    for method, path, body in [
        ("PATCH", f"/quotes/{qa['id']}", {"notes": "x"}), ("POST", f"/quotes/{qa['id']}/reopen", {"reason": "steal it"}),
        ("GET", f"/comparisons/{cmp_['id']}", None), ("GET", f"/comparisons/{cmp_['id']}/pdf", None),
        ("GET", f"/rfqs/{rfq['id']}/quotes", None),
    ]:
        res = await client.request(method, path, json=body, headers=b.admin.headers)
        assert res.status_code == 404, (method, path)


@pytest.mark.parametrize("role,can_quote,can_compare", [
    ("requester", False, False), ("buyer", True, True), ("approver", False, True), ("auditor", False, False),
])
async def test_quote_and_comparison_permissions(client, worker, role, can_quote, can_compare):
    o = await org(client)
    a = await qualified_supplier(client, o, "Northwind Metals")
    rfq = await sent_rfq(client, worker, o, [a])
    who = getattr(o, role)
    res = await client.post(f"/rfqs/{rfq['id']}/quotes", json={"supplier_id": a["id"], "currency": "EUR", "valid_until": DUE}, headers=who.headers)
    assert res.status_code == (201 if can_quote else 403)
    res = await client.post(f"/rfqs/{rfq['id']}/comparisons", json={}, headers=who.headers)
    assert res.status_code == (201 if can_compare else 403)
    assert (await client.get(f"/rfqs/{rfq['id']}/comparisons", headers=who.headers)).status_code == 200
