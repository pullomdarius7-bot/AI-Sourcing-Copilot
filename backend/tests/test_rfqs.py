import io
from datetime import date, timedelta
from email import policy
from email.parser import BytesParser

import pytest
from pypdf import PdfReader
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app import email as mail
from app.config import get_settings
from tests.rfq_helpers import (
    DUE,
    ITEMS,
    approved_rfq,
    create_supplier,
    draft_rfq,
    org,
    qualified_supplier,
    sent_rfq,
)

settings = get_settings()
YEAR = date.today().year


def pdf_text(data: bytes) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)


# --- Generation --------------------------------------------------------------------------------------------------
async def test_rfq_from_an_approved_requirement_extraction(client, worker):
    from tests.req_helpers import resolve_all, team

    t = await team(client, worker)
    run_id = t.run_id
    unapproved = await client.post("/rfqs", json={"title": "Brackets", "from_run_id": run_id}, headers=t.buyer.headers)
    assert unapproved.status_code == 409 and unapproved.json()["error"]["code"] == "run_not_approved"
    d = await resolve_all(client, t.buyer, run_id)
    reject_id = next(p["id"] for r in d["requirements"] for p in r["parameters"] if p["name"] == "surface_finish")
    await client.post(f"/parameters/{reject_id}/reset", headers=t.buyer.headers)
    await client.post(f"/parameters/{reject_id}/reject", json={"reason": "Not needed any more"}, headers=t.buyer.headers)
    assert (await client.post(f"/extraction-runs/{run_id}/approve", json={}, headers=t.approver.headers)).status_code == 200

    res = await client.post("/rfqs", json={"title": "Brackets", "from_run_id": run_id, "response_due_date": DUE}, headers=t.buyer.headers)
    assert res.status_code == 201, res.text
    rfq = res.json()
    assert rfq["number"] == f"RFQ-{YEAR}-0001" and rfq["status"] == "draft" and rfq["version"] == 1
    assert rfq["currency"] == settings.default_currency and rfq["source_run_id"] == run_id
    statements = [i["statement"] for i in rfq["items"]]
    assert len(rfq["items"]) == 7 and statements[-1] == "Supplier to state: warranty period"
    first = rfq["items"][0]
    assert first["quantity"] == 5000 and first["unit"] == "pcs" and first["priority"] == "mandatory"
    names = {p["name"]: p for p in first["parameters"]}
    assert "quantity" not in names and names["material"]["value"] == "AISI 304"
    assert names["grade_class"]["unknown"] is True and names["grade_class"]["value"] is None  # unknown stays unknown
    finish = next(i for i in rfq["items"] if i["statement"].startswith("Surface finish"))
    assert finish["parameters"] == []  # the rejected parameter never reaches suppliers
    assert [i["line"] for i in rfq["items"]] == list(range(1, 8))


async def test_numbers_are_sequential_per_organization(client):
    a = await org(client)
    b = await org(client, email="admin@beta.test", name="Beta", domain="beta.test")
    first = await draft_rfq(client, a, items=[])
    second = await draft_rfq(client, a, items=[])
    other = await draft_rfq(client, b, items=[])
    assert [first["number"], second["number"], other["number"]] == [f"RFQ-{YEAR}-0001", f"RFQ-{YEAR}-0002", f"RFQ-{YEAR}-0001"]


async def test_editing_lines_and_suppliers_in_draft(client):
    o = await org(client)
    s1 = await qualified_supplier(client, o, "Northwind Metals")
    rfq = await draft_rfq(client, o, [s1])
    assert [i["line"] for i in rfq["items"]] == [1, 2]
    second = rfq["items"][1]["id"]
    res = await client.patch(f"/rfqs/{rfq['id']}/items/{second}", json={"position": 0, "quantity": 2500}, headers=o.buyer.headers)
    assert [i["id"] for i in res.json()["items"]][0] == second and res.json()["items"][0]["quantity"] == 2500
    res = await client.delete(f"/rfqs/{rfq['id']}/items/{second}", headers=o.buyer.headers)
    assert len(res.json()["items"]) == 1 and res.json()["items"][0]["position"] == 0
    assert (await client.post(f"/rfqs/{rfq['id']}/items", json={"statement": "x"}, headers=o.buyer.headers)).status_code == 422
    assert (await client.post(f"/rfqs/{rfq['id']}/items", json={"statement": "Valid line", "quantity": 0}, headers=o.buyer.headers)).status_code == 422
    res = await client.delete(f"/rfqs/{rfq['id']}/suppliers/{s1['id']}", headers=o.buyer.headers)
    assert res.json()["suppliers"] == []
    patched = await client.patch(f"/rfqs/{rfq['id']}", json={"title": "Brackets, revised", "currency": "usd"}, headers=o.buyer.headers)
    assert patched.json()["title"] == "Brackets, revised" and patched.json()["currency"] == "USD"


async def test_blocked_and_disqualified_suppliers_cannot_be_invited(client):
    o = await org(client)
    s = await create_supplier(client, o.buyer, "Shady Parts")
    await client.post(f"/suppliers/{s['id']}/block", json={"reason": "Sanctions hit"}, headers=o.approver.headers)
    rfq = await draft_rfq(client, o)
    res = await client.post(f"/rfqs/{rfq['id']}/suppliers", json={"supplier_ids": [s["id"]]}, headers=o.buyer.headers)
    assert res.status_code == 409 and res.json()["error"]["code"] == "supplier_not_eligible"


async def test_supplier_suggestions_follow_categories_and_qualification(client):
    o = await org(client)
    metal = (await client.post("/supplier-categories", json={"name": "Metal"}, headers=o.buyer.headers)).json()
    sheet = (await client.post("/supplier-categories", json={"name": "Sheet metal", "parent_id": metal["id"]}, headers=o.buyer.headers)).json()
    await qualified_supplier(client, o, "Northwind Metals", category_ids=[sheet["id"]])
    await qualified_supplier(client, o, "Plastics Co")
    await create_supplier(client, o.buyer, "Not Yet Qualified", category_ids=[sheet["id"]])
    rfq = await draft_rfq(client, o, category_id=metal["id"])
    names = [x["name"] for x in (await client.get(f"/rfqs/{rfq['id']}/supplier-suggestions", headers=o.buyer.headers)).json()]
    assert names == ["Northwind Metals"]


# --- Approval workflow --------------------------------------------------------------------------------------------------
async def test_submit_requires_a_complete_rfq(client):
    o = await org(client)
    empty = await draft_rfq(client, o, items=[], response_due_date=None)
    assert any("Add at least one requirement line" in r for r in empty["readiness"])
    res = await client.post(f"/rfqs/{empty['id']}/submit", headers=o.buyer.headers)
    assert res.status_code == 409 and res.json()["error"]["code"] == "not_ready"
    for phrase in ("requirement line", "due date", "Invite at least one supplier"):
        assert phrase in res.json()["error"]["message"]
    prospect = await create_supplier(client, o.buyer, "Prospect Ltd")
    past = await draft_rfq(client, o, [prospect], response_due_date=(date.today() - timedelta(days=1)).isoformat())
    msg = (await client.post(f"/rfqs/{past['id']}/submit", headers=o.buyer.headers)).json()["error"]["message"]
    assert "must be in the future" in msg and "Prospect Ltd is not currently qualified" in msg


async def test_full_approval_workflow_locks_content(client):
    o = await org(client)
    s1 = await qualified_supplier(client, o, "Northwind Metals")
    rfq = await draft_rfq(client, o, [s1])
    assert rfq["readiness"] == []
    sub = (await client.post(f"/rfqs/{rfq['id']}/submit", headers=o.buyer.headers)).json()
    assert sub["status"] == "pending_approval" and len(sub["content_sha256"]) == 64 and sub["submitted_by"] == o.buyer.user_id
    for method, path, body in [("PATCH", f"/rfqs/{rfq['id']}", {"title": "Changed title"}),
                               ("POST", f"/rfqs/{rfq['id']}/items", ITEMS[0]),
                               ("POST", f"/rfqs/{rfq['id']}/suppliers", {"supplier_ids": [s1["id"]]})]:
        res = await client.request(method, path, json=body, headers=o.buyer.headers)
        assert res.status_code == 409 and res.json()["error"]["code"] == "rfq_locked", path
    assert (await client.post(f"/rfqs/{rfq['id']}/approve", json={}, headers=o.buyer.headers)).status_code == 403
    ret = await client.post(f"/rfqs/{rfq['id']}/return", json={"reason": "Add the drawing reference"}, headers=o.approver.headers)
    assert ret.json()["status"] == "draft" and ret.json()["version"] == 2 and ret.json()["content_sha256"] is None
    assert ret.json()["decision_note"] == "Add the drawing reference"
    await client.patch(f"/rfqs/{rfq['id']}", json={"message": "Drawing D-114 rev B applies."}, headers=o.buyer.headers)
    await client.post(f"/rfqs/{rfq['id']}/submit", headers=o.buyer.headers)
    ok = await client.post(f"/rfqs/{rfq['id']}/approve", json={"note": "Fine"}, headers=o.approver.headers)
    assert ok.status_code == 200 and ok.json()["status"] == "approved" and ok.json()["approved_by"] == o.approver.user_id
    events = [e["action"] for e in reversed((await client.get(f"/rfqs/{rfq['id']}/events", headers=o.auditor.headers)).json())]
    assert events == ["created", "item_added", "item_added", "suppliers_added", "submitted", "returned", "updated",
                      "submitted", "approved"]


async def test_creator_cannot_approve_their_own_rfq(client, monkeypatch):
    o = await org(client)
    s1 = await qualified_supplier(client, o, "Northwind Metals")
    res = await client.post("/rfqs", json={"title": "Admin RFQ", "response_due_date": DUE}, headers=o.admin.headers)
    rfq = res.json()
    await client.post(f"/rfqs/{rfq['id']}/items", json=ITEMS[0], headers=o.admin.headers)
    await client.post(f"/rfqs/{rfq['id']}/suppliers", json={"supplier_ids": [s1["id"]]}, headers=o.admin.headers)
    await client.post(f"/rfqs/{rfq['id']}/submit", headers=o.admin.headers)
    res = await client.post(f"/rfqs/{rfq['id']}/approve", json={}, headers=o.admin.headers)
    assert res.status_code == 403 and res.json()["error"]["code"] == "segregation_of_duties"
    monkeypatch.setattr(settings, "require_separate_approver", False)
    assert (await client.post(f"/rfqs/{rfq['id']}/approve", json={}, headers=o.admin.headers)).status_code == 200


async def test_content_tampered_after_submission_cannot_be_approved(client, admin_engine):
    o = await org(client)
    s1 = await qualified_supplier(client, o, "Northwind Metals")
    rfq = await draft_rfq(client, o, [s1])
    await client.post(f"/rfqs/{rfq['id']}/submit", headers=o.buyer.headers)
    async with admin_engine.begin() as conn:
        await conn.execute(text("UPDATE rfq_items SET quantity = 1 WHERE rfq_id = CAST(:r AS uuid)"), {"r": rfq["id"]})
    res = await client.post(f"/rfqs/{rfq['id']}/approve", json={}, headers=o.approver.headers)
    assert res.status_code == 409 and res.json()["error"]["code"] == "content_changed"


async def test_supplier_losing_qualification_blocks_approval_and_sending(client):
    o = await org(client)
    s1 = await qualified_supplier(client, o, "Northwind Metals")
    rfq = await approved_rfq(client, o, [s1])
    await client.post(f"/suppliers/{s1['id']}/block", json={"reason": "Quality escape"}, headers=o.approver.headers)
    res = await client.post(f"/rfqs/{rfq['id']}/send", headers=o.buyer.headers)
    assert res.status_code == 409 and "not currently qualified" in res.json()["error"]["message"]


# --- Packages & sending -------------------------------------------------------------------------------------------------------
async def test_send_generates_packages_and_emails_each_supplier(client, worker):
    o = await org(client)
    s1 = await qualified_supplier(client, o, "Northwind Metals", email="ann@northwind.test")
    s2 = await qualified_supplier(client, o, "Kessler GmbH", email="sales@kessler.test")
    await client.post(f"/suppliers/{s1['id']}/contacts", json={"name": "Bob", "email": "bob@northwind.test"}, headers=o.buyer.headers)
    rfq = await sent_rfq(client, worker, o, [s1, s2], message="Drawing D-114 applies.")
    assert rfq["status"] == "sent" and {s["status"] for s in rfq["suppliers"]} == {"sent"}
    packages = (await client.get(f"/rfqs/{rfq['id']}/packages", headers=o.auditor.headers)).json()
    assert len(packages) == 2 and {p["send_status"] for p in packages} == {"sent"}
    nw = next(p for p in packages if p["supplier_name"] == "Northwind Metals")
    assert nw["to_addresses"] == ["ann@northwind.test", "bob@northwind.test"] and nw["rfq_version"] == 1
    assert nw["manifest"]["content_sha256"] == rfq["content_sha256"]
    assert [a["filename"] for a in nw["manifest"]["attachments"]] == [f"{rfq['number']}-v1.pdf", f"{rfq['number']}-v1.json"]

    sent = [m for m in mail.outbox if m["subject"].startswith(rfq["number"])]
    assert len(sent) == 2 and all("Request for quotation: Stainless brackets" in m["subject"] for m in sent)
    nw_mail = next(m for m in sent if "ann@northwind.test" in m["to"])
    assert "Drawing D-114 applies." in nw_mail["body"] and f"{rfq['number']}-v1.pdf" in nw_mail["attachments"]

    eml = await client.get(f"/rfqs/{rfq['id']}/packages/{nw['id']}/download", headers=o.buyer.headers)
    assert eml.status_code == 200 and eml.headers["content-type"].startswith("message/rfc822")
    msg = BytesParser(policy=policy.SMTP).parsebytes(eml.content)
    assert msg["Reply-To"] == f"Buyer User <{o.buyer.email}>" and msg["X-RFQ-Content-SHA256"] == rfq["content_sha256"]
    pdf = next(a for a in msg.iter_attachments() if a.get_filename().endswith(".pdf")).get_content()
    text_ = pdf_text(pdf)
    assert rfq["number"] in text_ and "DRAFT" not in text_ and "grade class: not specified" in text_

    events = [e["action"] for e in (await client.get(f"/rfqs/{rfq['id']}/events", headers=o.buyer.headers)).json()]
    assert events.count("package_generated") == 2 and events.count("package_sent") == 2 and "sent" in events
    assert (await client.post(f"/rfqs/{rfq['id']}/send", headers=o.buyer.headers)).status_code == 409


async def test_packages_can_be_generated_for_manual_sending_before_send(client):
    o = await org(client)
    s1 = await qualified_supplier(client, o, "Northwind Metals")
    rfq = await approved_rfq(client, o, [s1])
    first = (await client.post(f"/rfqs/{rfq['id']}/packages", headers=o.buyer.headers)).json()
    again = (await client.post(f"/rfqs/{rfq['id']}/packages", headers=o.buyer.headers)).json()
    assert len(first) == 1 and first[0]["id"] == again[0]["id"] and first[0]["send_status"] == "not_sent"
    draft = await draft_rfq(client, o, [s1])
    assert (await client.post(f"/rfqs/{draft['id']}/packages", headers=o.buyer.headers)).status_code == 409


async def test_tampered_package_is_never_sent(client, worker):
    from app.storage import get_storage

    o = await org(client)
    s1 = await qualified_supplier(client, o, "Northwind Metals")
    rfq = await approved_rfq(client, o, [s1])
    pkg = (await client.post(f"/rfqs/{rfq['id']}/packages", headers=o.buyer.headers)).json()[0]
    key = f"{o.admin.org_id}/rfq-packages/{rfq['id']}/v1/{s1['id']}.eml"
    await get_storage().put(key, b"Subject: changed\r\n\r\nWire money to account 123", "message/rfc822")
    before = len(mail.outbox)
    await client.post(f"/rfqs/{rfq['id']}/send", headers=o.buyer.headers)
    await worker.drain()
    assert len(mail.outbox) == before
    pkg = (await client.get(f"/rfqs/{rfq['id']}/packages", headers=o.buyer.headers)).json()[0]
    assert pkg["send_status"] == "failed" and "integrity check failed" in pkg["send_error"]
    detail = (await client.get(f"/rfqs/{rfq['id']}", headers=o.buyer.headers)).json()
    assert detail["suppliers"][0]["status"] == "failed"


async def test_mail_outage_retries_then_fails_and_can_be_resent(client, worker, monkeypatch):
    import app.email as email_module

    o = await org(client)
    s1 = await qualified_supplier(client, o, "Northwind Metals")
    rfq = await approved_rfq(client, o, [s1])
    real = email_module.send_message

    async def down(msg):
        raise ConnectionRefusedError("SMTP down")

    monkeypatch.setattr(email_module, "send_message", down)
    await client.post(f"/rfqs/{rfq['id']}/send", headers=o.buyer.headers)
    await worker.drain()
    pkg = (await client.get(f"/rfqs/{rfq['id']}/packages", headers=o.buyer.headers)).json()[0]
    assert pkg["send_status"] == "failed" and "SMTP down" in pkg["send_error"]
    job = next(j for j in (await client.get("/jobs?status=dead", headers=o.admin.headers)).json() if j["kind"] == "rfq.send_package")
    assert job["attempts"] == settings.job_max_attempts
    monkeypatch.setattr(email_module, "send_message", real)
    res = await client.post(f"/rfqs/{rfq['id']}/packages/{pkg['id']}/resend", headers=o.buyer.headers)
    assert res.status_code == 200 and res.json()[0]["send_status"] == "queued"
    await worker.drain()
    assert (await client.get(f"/rfqs/{rfq['id']}/packages", headers=o.buyer.headers)).json()[0]["send_status"] == "sent"
    assert (await client.post(f"/rfqs/{rfq['id']}/packages/{pkg['id']}/resend", headers=o.buyer.headers)).status_code == 409


async def test_cancelling_stops_queued_packages(client, worker):
    o = await org(client)
    s1 = await qualified_supplier(client, o, "Northwind Metals")
    rfq = await approved_rfq(client, o, [s1])
    await client.post(f"/rfqs/{rfq['id']}/send", headers=o.buyer.headers)
    res = await client.post(f"/rfqs/{rfq['id']}/cancel", json={"reason": "Project stopped"}, headers=o.buyer.headers)
    assert res.json()["status"] == "cancelled"
    before = len(mail.outbox)
    await worker.drain()
    assert len(mail.outbox) == before
    assert (await client.get(f"/rfqs/{rfq['id']}/packages", headers=o.buyer.headers)).json()[0]["send_status"] == "failed"
    assert (await client.post(f"/rfqs/{rfq['id']}/cancel", json={"reason": "again please"}, headers=o.buyer.headers)).status_code == 409


async def test_close_rfq(client, worker):
    o = await org(client)
    s1 = await qualified_supplier(client, o, "Northwind Metals")
    draft = await draft_rfq(client, o, [s1])
    assert (await client.post(f"/rfqs/{draft['id']}/close", json={}, headers=o.buyer.headers)).status_code == 409
    rfq = await sent_rfq(client, worker, o, [s1])
    res = await client.post(f"/rfqs/{rfq['id']}/close", json={"note": "Deadline passed"}, headers=o.buyer.headers)
    assert res.json()["status"] == "closed" and res.json()["closed_at"]


# --- Exports ------------------------------------------------------------------------------------------------------------------------
async def test_pdf_and_json_exports(client):
    o = await org(client)
    s1 = await qualified_supplier(client, o, "Northwind Metals")
    rfq = await draft_rfq(client, o, [s1])
    res = await client.get(f"/rfqs/{rfq['id']}/pdf", headers=o.auditor.headers)
    assert res.status_code == 200 and res.headers["content-type"] == "application/pdf"
    assert res.headers["content-disposition"].endswith(f"{rfq['number']}-v1.pdf")
    assert "DRAFT" in pdf_text(res.content)
    approved = await approved_rfq(client, o, [s1])
    text_ = pdf_text((await client.get(f"/rfqs/{approved['id']}/pdf", headers=o.auditor.headers)).content)
    assert "DRAFT" not in text_ and approved["content_sha256"][:16] in text_
    data = (await client.get(f"/rfqs/{approved['id']}/export.json", headers=o.auditor.headers)).json()
    assert data["rfq"]["number"] == approved["number"] and data["content_sha256"] == approved["content_sha256"]
    assert data["suppliers"] == [{"code": s1["code"], "name": "Northwind Metals", "status": "invited"}]
    actions = [r["action"] for r in (await client.get("/audit-logs?action=rfq.exported", headers=o.admin.headers)).json()]
    assert len(actions) == 3


# --- Permissions, isolation, database guarantees ------------------------------------------------------------------------------------
@pytest.mark.parametrize("role,can_manage,can_approve", [
    ("requester", False, False), ("buyer", True, False), ("approver", False, True), ("auditor", False, False),
])
async def test_rfq_permissions(client, role, can_manage, can_approve):
    o = await org(client)
    s1 = await qualified_supplier(client, o, "Northwind Metals")
    who = getattr(o, role)
    rfq = await draft_rfq(client, o, [s1])
    assert (await client.get("/rfqs", headers=who.headers)).status_code == 200
    assert (await client.get(f"/rfqs/{rfq['id']}", headers=who.headers)).status_code == 200
    assert (await client.post("/rfqs", json={"title": "Mine please"}, headers=who.headers)).status_code == (201 if can_manage else 403)
    await client.post(f"/rfqs/{rfq['id']}/submit", headers=o.buyer.headers)
    res = await client.post(f"/rfqs/{rfq['id']}/approve", json={}, headers=who.headers)
    assert res.status_code == (200 if can_approve else 403)


async def test_rfqs_are_isolated_between_organizations(client, worker):
    a = await org(client)
    b = await org(client, email="admin@beta.test", name="Beta", domain="beta.test")
    s1 = await qualified_supplier(client, a, "Northwind Metals")
    rfq = await sent_rfq(client, worker, a, [s1])
    pkg = (await client.get(f"/rfqs/{rfq['id']}/packages", headers=a.buyer.headers)).json()[0]
    for method, path, body in [
        ("GET", f"/rfqs/{rfq['id']}", None), ("GET", f"/rfqs/{rfq['id']}/pdf", None),
        ("GET", f"/rfqs/{rfq['id']}/packages/{pkg['id']}/download", None), ("POST", f"/rfqs/{rfq['id']}/close", {}),
        ("POST", f"/rfqs/{rfq['id']}/quotes", {"supplier_id": s1["id"], "lines": []}), ("GET", f"/rfqs/{rfq['id']}/events", None),
    ]:
        res = await client.request(method, path, json=body, headers=b.buyer.headers)
        assert res.status_code == 404, (method, path)
    assert (await client.get("/rfqs", headers=b.buyer.headers)).json() == {"items": [], "total": 0}
    other = await draft_rfq(client, b)
    res = await client.post(f"/rfqs/{other['id']}/suppliers", json={"supplier_ids": [s1["id"]]}, headers=b.buyer.headers)
    assert res.status_code == 400  # another organization's supplier simply does not exist here


async def test_database_refuses_unapproved_sending_and_unrecorded_awards(client, worker, admin_engine):
    o = await org(client)
    s1 = await qualified_supplier(client, o, "Northwind Metals")
    draft = await draft_rfq(client, o, [s1])
    sent = await sent_rfq(client, worker, o, [s1])
    async with admin_engine.begin() as conn:
        with pytest.raises(IntegrityError):
            await conn.execute(text("UPDATE rfqs SET status = 'sent' WHERE id = CAST(:r AS uuid)"), {"r": draft["id"]})
    async with admin_engine.begin() as conn:
        with pytest.raises(IntegrityError):
            await conn.execute(text("UPDATE rfqs SET status = 'awarded' WHERE id = CAST(:r AS uuid)"), {"r": sent["id"]})


async def test_history_tables_are_append_only_and_tenant_scoped(client, worker):
    import uuid

    from sqlalchemy.exc import DBAPIError

    from app.db import SessionLocal, set_tenant

    o = await org(client)
    s1 = await qualified_supplier(client, o, "Northwind Metals")
    await sent_rfq(client, worker, o, [s1])
    async with SessionLocal() as s:
        for table in ("suppliers", "rfqs", "rfq_packages", "domain_events", "counters", "qualification_criteria"):
            assert await s.scalar(text(f"SELECT count(*) FROM {table}")) == 0, table
    async with SessionLocal() as s:
        await set_tenant(s, uuid.UUID(o.admin.org_id))
        assert await s.scalar(text("SELECT count(*) FROM domain_events")) > 5
        with pytest.raises(DBAPIError):
            await s.execute(text("UPDATE domain_events SET action = 'tampered'"))
        await s.rollback()
        await set_tenant(s, uuid.UUID(o.admin.org_id))
        with pytest.raises(DBAPIError):
            await s.execute(text("DELETE FROM domain_events"))
