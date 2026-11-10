import uuid

import pytest

from app.config import get_settings
from tests import extraction_fixtures as fx
from tests.helpers import add_member, register_admin
from tests.req_helpers import all_params, detail, extract, param, ready_document, requirement, resolve_all, team

settings = get_settings()


async def post(client, who, path, body=None):
    return await client.post(path, json=body if body is not None else {}, headers=who.headers)


# --- Verify / reject / reset -------------------------------------------------------------------------
async def test_verify_records_who_and_when_and_cannot_be_repeated(client, worker):
    t = await team(client, worker)
    qty = param(t.detail, "quantity")
    res = await post(client, t.buyer, f"/parameters/{qty['id']}/verify", {"note": "matches the RFQ"})
    body = res.json()
    assert res.status_code == 200 and body["validation_state"] == "verified" and body["verified"] is True
    assert body["verified_by"] == t.buyer.user_id and body["verified_at"] and body["review_note"] == "matches the RFQ"
    assert body["confidence"] == qty["confidence"]  # review never rewrites the machine's confidence
    again = await post(client, t.buyer, f"/parameters/{qty['id']}/verify")
    assert again.status_code == 409 and again.json()["error"]["code"] == "invalid_state"
    reset = await post(client, t.buyer, f"/parameters/{qty['id']}/reset")
    assert reset.json()["validation_state"] == "unverified" and reset.json()["verified_by"] is None
    assert (await post(client, t.buyer, f"/parameters/{qty['id']}/reset")).status_code == 409


async def test_rejecting_needs_a_reason_and_removes_the_value_from_use(client, worker):
    t = await team(client, worker)
    pid = param(t.detail, "surface_finish")["id"]
    assert (await post(client, t.buyer, f"/parameters/{pid}/reject", {"reason": "no"})).status_code == 422
    assert (await post(client, t.buyer, f"/parameters/{pid}/reject")).status_code == 422
    res = await post(client, t.buyer, f"/parameters/{pid}/reject", {"reason": "Customer dropped this finish"})
    assert res.status_code == 200 and res.json()["validation_state"] == "rejected" and res.json()["verified"] is False
    assert (await post(client, t.buyer, f"/parameters/{pid}/reject", {"reason": "again please"})).status_code == 409


async def test_rejecting_a_requirement_rejects_everything_under_it(client, worker):
    t = await team(client, worker)
    req = requirement(t.detail, "The supplier shall deliver")
    res = await post(client, t.buyer, f"/requirements/{req['id']}/reject", {"reason": "Superseded by drawing"})
    assert res.status_code == 200
    assert res.json()["validation_state"] == "rejected" and {p["validation_state"] for p in res.json()["parameters"]} == {"rejected"}
    assert len(res.json()["parameters"]) == 3
    events = (await client.get(f"/extraction-runs/{t.run_id}/events", headers=t.buyer.headers)).json()
    assert sum(1 for e in events if e["action"] == "reject_cascade") == 3


async def test_verifying_a_requirement_statement(client, worker):
    t = await team(client, worker)
    req = requirement(t.detail, "The supplier shall deliver")
    res = await post(client, t.buyer, f"/requirements/{req['id']}/verify")
    assert res.status_code == 200 and res.json()["verified"] is True
    assert all(p["validation_state"] == "unverified" for p in res.json()["parameters"])  # parameters are reviewed separately


# --- Corrections must carry evidence -------------------------------------------------------------------------
async def test_correcting_an_unknown_with_a_grounded_quote(client, worker):
    t = await team(client, worker)
    grade = param(t.detail, "grade_class")
    assert grade["unknown"] is True
    res = await post(client, t.buyer, f"/parameters/{grade['id']}/correct", {"text": "AISI 304", "source_quote": "material AISI 304", "note": "stated in clause 1"})
    body = res.json()
    assert res.status_code == 200 and body["validation_state"] == "corrected" and body["verified"] is True
    assert (body["unknown"], body["value_text"], body["grounding_status"]) == (False, "AISI 304", "grounded")
    doc_text = (await client.get(f"/documents/{t.doc_id}/text", headers=t.buyer.headers)).json()["text"]
    assert doc_text[body["source_start"] : body["source_end"]] == "material AISI 304"
    assert body["original"]["unknown"] is True and body["original"]["value_text"] is None  # the machine's version is kept
    # undoing the review restores what the machine extracted
    back = (await post(client, t.buyer, f"/parameters/{grade['id']}/reset")).json()
    assert back["unknown"] is True and back["value_text"] is None and back["source_quote"] is None and back["original"] is None


@pytest.mark.parametrize(
    "body,status,code",
    [
        ({"text": "AISI 304", "source_quote": "The supplier shall provide a warranty"}, 422, "ungrounded_correction"),
        ({"text": "AISI 316", "source_quote": "material AISI 304"}, 422, "value_not_in_quote"),
        ({"value_type": "number", "number": 3, "unit": "kg", "source_quote": "thickness of 3 mm"}, 422, "unit_not_in_quote"),
        ({"value_type": "number", "number": 4, "source_quote": "thickness of 3 mm"}, 422, "value_not_in_quote"),
        ({"text": "AISI 304"}, 422, "source_required"),
        ({"evidence": "manual", "text": "AISI 304", "note": "short"}, 422, "note_required"),
        ({"text": "  "}, 400, "missing_value"),
    ],
)
async def test_corrections_without_real_evidence_are_refused(client, worker, body, status, code):
    t = await team(client, worker)
    pid = param(t.detail, "grade_class")["id"]
    res = await post(client, t.buyer, f"/parameters/{pid}/correct", body)
    assert res.status_code == status and res.json()["error"]["code"] == code
    unchanged = param(await detail(client, t.buyer, t.run_id), "grade_class")
    assert unchanged["unknown"] is True and unchanged["validation_state"] == "unverified"


async def test_manual_evidence_is_allowed_only_with_an_explanation_and_is_marked(client, worker):
    t = await team(client, worker)
    pid = param(t.detail, "grade_class")["id"]
    res = await post(client, t.buyer, f"/parameters/{pid}/correct", {
        "evidence": "manual", "text": "Grade A", "note": "Confirmed by phone with the buyer on 2 Nov"})
    body = res.json()
    assert res.status_code == 200 and body["grounding_status"] == "manual" and body["flags"] == ["manual_evidence"]
    assert body["source_start"] is None and body["value_text"] == "Grade A" and body["validation_state"] == "corrected"


async def test_human_can_mark_a_value_unknown(client, worker):
    t = await team(client, worker)
    pid = param(t.detail, "payment_terms")["id"]
    res = await post(client, t.buyer, f"/parameters/{pid}/correct", {"unknown": True, "note": "Clause is ambiguous"})
    body = res.json()
    assert body["unknown"] is True and body["value_text"] is None and body["source_quote"] is None
    assert body["flags"] == ["human_marked_unknown"] and body["original"]["value_text"] == "Net 45 days"


async def test_numeric_correction_with_qualifier(client, worker):
    t = await team(client, worker)
    pid = param(t.detail, "thickness")["id"]
    res = await post(client, t.buyer, f"/parameters/{pid}/correct", {
        "value_type": "number", "number": 3, "unit": "mm", "qualifier": "min", "source_quote": "thickness of 3 mm"})
    assert res.json()["display_value"] == "\u2265 3 mm" and res.json()["qualifier"] == "min"


async def test_requirement_corrections_are_checked_against_the_quote(client, worker):
    t = await team(client, worker)
    finish = requirement(t.detail, "Surface finish")
    bad_statement = await post(client, t.buyer, f"/requirements/{finish['id']}/correct", {"statement": "Finish must be mirror polished"})
    assert bad_statement.status_code == 422 and bad_statement.json()["error"]["code"] == "statement_not_in_quote"
    bad_priority = await post(client, t.buyer, f"/requirements/{finish['id']}/correct", {"priority": "mandatory"})
    assert bad_priority.status_code == 422 and bad_priority.json()["error"]["code"] == "priority_not_in_quote"
    bad_category = await post(client, t.buyer, f"/requirements/{finish['id']}/correct", {"category": "magic"})
    assert bad_category.status_code == 400
    ok = await post(client, t.buyer, f"/requirements/{finish['id']}/correct", {"category": "packaging", "priority": "preferred", "note": "recategorised"})
    assert ok.status_code == 200 and ok.json()["category"] == "packaging" and ok.json()["validation_state"] == "corrected"
    assert ok.json()["original"]["category"] == "quality"


# --- Bulk verification ----------------------------------------------------------------------------------------
async def test_bulk_verify_only_accepts_high_confidence_unflagged_grounded_values(client, worker):
    script = fx.good_extraction()
    script["requirements"][4]["parameters"][0]["confidence"] = 0.5  # certification: low confidence
    t = await team(client, worker, [script])
    ids = {p["name"]: p["id"] for p in all_params(t.detail)}
    res = await post(client, t.buyer, "/parameters/bulk-verify", {"ids": list(ids.values()) + [str(uuid.uuid4())]})
    body = res.json()
    skipped = {s["id"]: s["reason"] for s in body["skipped"]}
    assert res.status_code == 200
    assert skipped[ids["grade_class"]] == "not_a_grounded_value" and skipped[ids["warranty_period"]] == "not_a_grounded_value"
    assert skipped[ids["surface_finish"]] == "has_flags" and skipped[ids["certification"]] == "below_confidence_threshold"
    assert "not_found" in skipped.values()
    assert {ids[n] for n in ("quantity", "material", "thickness", "tolerance", "incoterms", "payment_terms")} <= set(body["verified"])
    d = await detail(client, t.buyer, t.run_id)
    assert param(d, "quantity")["validation_state"] == "verified" and param(d, "surface_finish")["validation_state"] == "unverified"
    again = await post(client, t.buyer, "/parameters/bulk-verify", {"ids": [ids["quantity"]]})
    assert again.json()["skipped"] == [{"id": ids["quantity"], "reason": "not_unverified"}]
    assert (await post(client, t.buyer, "/parameters/bulk-verify", {"ids": []})).status_code == 422


# --- Run status & approval --------------------------------------------------------------------------------------
async def test_run_becomes_ready_when_everything_is_resolved(client, worker):
    t = await team(client, worker)
    assert t.detail["status"] == "pending_review"
    d = await resolve_all(client, t.buyer, t.run_id)
    assert d["status"] == "ready_for_approval" and d["counts"]["unresolved"] == 0 and d["counts"]["verified"] == 12
    await post(client, t.buyer, f"/parameters/{param(d, 'quantity')['id']}/reset")
    assert (await detail(client, t.buyer, t.run_id))["status"] == "pending_review"


async def test_approval_requires_every_item_to_be_resolved(client, worker):
    t = await team(client, worker)
    res = await post(client, t.approver, f"/extraction-runs/{t.run_id}/approve")
    assert res.status_code == 409 and res.json()["error"]["code"] == "unresolved_items" and "18" in res.json()["error"]["message"]


async def test_full_workflow_to_an_approved_locked_export(client, worker):
    t = await team(client, worker)
    d = await resolve_all(client, t.buyer, t.run_id)
    await post(client, t.buyer, f"/parameters/{param(d, 'surface_finish')['id']}/reset")
    await post(client, t.buyer, f"/parameters/{param(d, 'surface_finish')['id']}/reject", {"reason": "Not required any more"})
    assert (await client.get(f"/extraction-runs/{t.run_id}/export", headers=t.approver.headers)).status_code == 409  # not approved yet

    res = await post(client, t.approver, f"/extraction-runs/{t.run_id}/approve", {"note": "Looks right"})
    assert res.status_code == 200 and res.json()["status"] == "approved" and res.json()["approved_by"] == t.approver.user_id
    assert res.json()["decision_note"] == "Looks right"

    # locked
    qty = param(d, "quantity")["id"]
    for path, body in [(f"/parameters/{qty}/reset", None), (f"/parameters/{qty}/reject", {"reason": "changed my mind"}),
                       (f"/parameters/{qty}/correct", {"unknown": True}), (f"/extraction-runs/{t.run_id}/reject", {"reason": "too late"})]:
        r = await post(client, t.buyer if "extraction" not in path else t.approver, path, body)
        assert r.status_code == 409 and r.json()["error"]["code"] == "run_locked", path
    assert (await post(client, t.approver, f"/extraction-runs/{t.run_id}/approve")).status_code == 409

    export = (await client.get(f"/extraction-runs/{t.run_id}/export", headers=t.auditor.headers)).json()
    assert export["document"]["filename"] == "rfq.txt" and export["approved_by"] == t.approver.user_id
    names = [p["name"] for r in export["requirements"] for p in r["parameters"]]
    assert "surface_finish" not in names and "quantity" in names  # rejected values never leave the system
    unknown = [p for r in export["requirements"] for p in r["parameters"] if p["unknown"]] + export["unknown_parameters"]
    assert unknown and all(p["value"] is None and p["source"]["quote"] is None for p in unknown)  # unknown stays unknown
    qty_export = next(p for r in export["requirements"] for p in r["parameters"] if p["name"] == "quantity")
    assert qty_export["value"] == 5000 and qty_export["unit"] == "pcs" and qty_export["verified"] is True
    assert qty_export["source"]["quote"] == "deliver 5,000 pcs" and qty_export["confidence"] > 0.8

    # an approved extraction protects its document
    res = await client.delete(f"/documents/{t.doc_id}", headers=t.admin.headers)
    assert res.status_code == 409 and res.json()["error"]["code"] == "has_approved_requirements"
    assert (await client.get("/review/queue", headers=t.buyer.headers)).json()["total"] == 0


async def test_only_approvers_can_decide_a_run(client, worker):
    t = await team(client, worker)
    await resolve_all(client, t.buyer, t.run_id)
    for who in (t.requester, t.buyer, t.auditor):
        assert (await post(client, who, f"/extraction-runs/{t.run_id}/approve")).status_code == 403
        assert (await post(client, who, f"/extraction-runs/{t.run_id}/reject", {"reason": "nope nope"})).status_code == 403


async def test_approver_who_reviewed_cannot_approve(client, worker):
    t = await team(client, worker)
    first = param(t.detail, "quantity")["id"]
    assert (await post(client, t.approver, f"/parameters/{first}/verify")).status_code == 200
    await resolve_all(client, t.buyer, t.run_id)
    res = await post(client, t.approver, f"/extraction-runs/{t.run_id}/approve")
    assert res.status_code == 403 and res.json()["error"]["code"] == "segregation_of_duties"
    assert (await post(client, t.admin, f"/extraction-runs/{t.run_id}/approve")).status_code == 200  # an uninvolved approver may


async def test_whoever_started_the_extraction_cannot_approve_it(client, worker, monkeypatch):
    admin = await register_admin(client)
    buyer = await add_member(client, admin, "buyer@acme.test", "buyer")
    doc_id = await ready_document(client, worker, admin)
    run_id, _ = await extract(client, worker, admin, doc_id)
    await resolve_all(client, buyer, run_id)
    res = await post(client, admin, f"/extraction-runs/{run_id}/approve")
    assert res.status_code == 403 and res.json()["error"]["code"] == "segregation_of_duties"
    monkeypatch.setattr(settings, "require_separate_approver", False)  # small teams can opt out
    assert (await post(client, admin, f"/extraction-runs/{run_id}/approve")).status_code == 200


async def test_rejecting_a_run(client, worker):
    t = await team(client, worker)
    assert (await post(client, t.approver, f"/extraction-runs/{t.run_id}/reject", {"reason": "x"})).status_code == 422
    res = await post(client, t.approver, f"/extraction-runs/{t.run_id}/reject", {"reason": "Wrong document version"})
    assert res.status_code == 200 and res.json()["status"] == "rejected" and res.json()["decision_note"] == "Wrong document version"
    assert (await post(client, t.buyer, f"/parameters/{param(t.detail, 'quantity')['id']}/verify")).status_code == 409
    assert (await client.get("/review/queue", headers=t.buyer.headers)).json()["total"] == 0


async def test_review_actions_need_the_review_permission(client, worker):
    t = await team(client, worker)
    pid = param(t.detail, "quantity")["id"]
    for who in (t.requester, t.auditor):
        assert (await post(client, who, f"/parameters/{pid}/verify")).status_code == 403
        assert (await post(client, who, "/parameters/bulk-verify", {"ids": [pid]})).status_code == 403
        assert (await client.get("/review/queue", headers=who.headers)).status_code == 403
        assert (await client.get(f"/extraction-runs/{t.run_id}", headers=who.headers)).status_code == 200  # but they can read
    assert (await post(client, t.approver, f"/parameters/{pid}/verify")).status_code == 200


# --- History ------------------------------------------------------------------------------------------------------------
async def test_every_action_is_in_the_event_history_and_audit_log(client, worker):
    t = await team(client, worker)
    pid = param(t.detail, "quantity")["id"]
    await post(client, t.buyer, f"/parameters/{pid}/verify", {"note": "ok"})
    await post(client, t.buyer, f"/parameters/{pid}/reset")
    await post(client, t.buyer, f"/parameters/{pid}/reject", {"reason": "duplicate of line 4"})
    events = (await client.get(f"/extraction-runs/{t.run_id}/events", headers=t.auditor.headers)).json()
    mine = [e for e in reversed(events) if e["item_id"] == pid]
    assert [(e["action"], e["from_state"], e["to_state"]) for e in mine] == [
        ("verify", "unverified", "verified"), ("reset", "verified", "unverified"), ("reject", "unverified", "rejected")]
    assert all(e["actor_email"] == "buyer@acme.test" for e in mine)
    assert mine[0]["before"]["validation_state"] == "unverified" and mine[0]["after"]["validation_state"] == "verified"
    assert mine[0]["note"] == "ok" and mine[2]["note"] == "duplicate of line 4"
    actions = [r["action"] for r in (await client.get("/audit-logs?action=requirements.&limit=200", headers=t.admin.headers)).json()]
    assert {"requirements.verify", "requirements.reset", "requirements.reject", "requirements.extracted"} <= set(actions)


# --- Review queue ------------------------------------------------------------------------------------------------------------
async def test_review_queue_lists_the_riskiest_items_first(client, worker):
    t = await team(client, worker)
    q = (await client.get("/review/queue?limit=100", headers=t.buyer.headers)).json()
    assert q["total"] == 18 and len(q["items"]) == 18
    items = q["items"]
    assert items[0]["flags"] and items[1]["flags"] and not items[2]["flags"]  # flagged (hedged) items first
    assert items[2]["confidence"] == 0 and items[2]["grounding_status"] == "unknown"
    confidences = [i["confidence"] for i in items[2:]]
    assert confidences == sorted(confidences)
    qty = next(i for i in items if i["label"] == "quantity")
    assert qty["value"] == "5000 pcs" and qty["filename"] == "rfq.txt" and qty["requirement_statement"].startswith("Deliver 5,000")
    assert qty["source_quote"] == "deliver 5,000 pcs" and qty["confidence_bucket"] == "high"


async def test_review_queue_filters_paging_and_resolution(client, worker):
    t = await team(client, worker)

    async def queue(qs=""):
        return (await client.get(f"/review/queue?{qs}", headers=t.buyer.headers)).json()

    assert (await queue("item_type=requirement"))["total"] == 6
    assert (await queue("item_type=parameter"))["total"] == 12
    assert (await queue("flagged=true"))["total"] == 2
    assert (await queue("max_confidence=0.5"))["total"] == 2
    assert (await queue(f"document_id={t.doc_id}"))["total"] == 18
    assert (await queue(f"document_id={uuid.uuid4()}"))["total"] == 0
    assert (await queue(f"run_id={t.run_id}"))["total"] == 18
    page = await queue("limit=5&offset=15")
    assert page["total"] == 18 and len(page["items"]) == 3
    assert (await client.get("/review/queue?max_confidence=3", headers=t.buyer.headers)).status_code == 422
    pid = param(t.detail, "quantity")["id"]
    await post(client, t.buyer, f"/parameters/{pid}/verify")
    after = await queue("limit=100")
    assert after["total"] == 17 and pid not in [i["id"] for i in after["items"]]
