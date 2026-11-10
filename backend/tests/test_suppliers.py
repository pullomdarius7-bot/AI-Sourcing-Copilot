import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.config import get_settings
from tests.rfq_helpers import create_supplier, criteria, org, qualify

settings = get_settings()


async def test_create_supplier_profile_with_code_contacts_and_categories(client):
    o = await org(client)
    cat = (await client.post("/supplier-categories", json={"name": "Sheet metal"}, headers=o.buyer.headers)).json()
    s = await create_supplier(client, o.buyer, "Northwind Metals", category_ids=[cat["id"]], country="de", currency="eur",
                              tags=["Stainless", " stainless", "Laser"])
    assert s["code"] == "SUP-0001" and s["status"] == "prospect" and s["country"] == "DE" and s["currency"] == "EUR"
    assert s["tags"] == ["laser", "stainless"] and [c["name"] for c in s["categories"]] == ["Sheet metal"]
    assert s["contacts"][0]["is_primary"] is True and s["contacts"][0]["email"] == "sales@northwindmetals.test"
    assert s["qualification"]["qualified"] is False and s["qualification"]["ready"] is False
    second = await create_supplier(client, o.buyer, "Kessler GmbH")
    assert second["code"] == "SUP-0002"
    cats = (await client.get("/supplier-categories", headers=o.buyer.headers)).json()
    assert cats[0]["supplier_count"] == 1


async def test_supplier_validation_and_duplicates(client):
    o = await org(client)
    await create_supplier(client, o.buyer, "Northwind Metals")
    dup = await client.post("/suppliers", json={"name": "northwind metals"}, headers=o.buyer.headers)
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "supplier_exists"
    for bad in ({"name": "X"}, {"name": "Valid", "country": "DEU"}, {"name": "Valid", "currency": "EURO"},
                {"name": "Valid", "category_ids": [str(uuid.uuid4())]},
                {"name": "Valid", "contacts": [{"name": "a", "email": "a@x.test"}, {"name": "b", "email": "A@x.test"}]}):
        res = await client.post("/suppliers", json=bad, headers=o.buyer.headers)
        assert res.status_code in (400, 422), bad
    dup_cat = await client.post("/supplier-categories", json={"name": "Castings"}, headers=o.buyer.headers)
    again = await client.post("/supplier-categories", json={"name": "castings"}, headers=o.buyer.headers)
    assert dup_cat.status_code == 201 and again.status_code == 409


async def test_update_profile_categories_and_search(client):
    o = await org(client)
    a = (await client.post("/supplier-categories", json={"name": "Castings"}, headers=o.buyer.headers)).json()
    b = (await client.post("/supplier-categories", json={"name": "Machining"}, headers=o.buyer.headers)).json()
    s = await create_supplier(client, o.buyer, "Orion Cast", country="CN")
    res = await client.patch(f"/suppliers/{s['id']}", json={"payment_terms": "Net 60", "typical_lead_time_days": 30, "tags": ["foundry"]}, headers=o.buyer.headers)
    assert res.status_code == 200 and res.json()["payment_terms"] == "Net 60" and res.json()["tags"] == ["foundry"]
    res = await client.put(f"/suppliers/{s['id']}/categories", json={"category_ids": [a["id"], b["id"]]}, headers=o.buyer.headers)
    assert {c["name"] for c in res.json()["categories"]} == {"Castings", "Machining"}
    await create_supplier(client, o.buyer, "Kessler GmbH", country="DE")
    lst = lambda qs: client.get(f"/suppliers?{qs}", headers=o.buyer.headers)  # noqa: E731
    assert (await lst(f"category_id={a['id']}")).json()["total"] == 1
    assert (await lst("country=de")).json()["items"][0]["name"] == "Kessler GmbH"
    assert (await lst("q=SUP-0001")).json()["items"][0]["name"] == "Orion Cast"
    assert (await lst("tag=foundry")).json()["total"] == 1
    assert (await lst("q=%25")).json()["total"] == 0
    assert (await lst("status=bogus")).status_code == 422
    events = (await client.get(f"/suppliers/{s['id']}/events", headers=o.auditor.headers)).json()
    assert [e["action"] for e in reversed(events)] == ["created", "updated", "categories_set"]
    assert events[1]["data"]["payment_terms"] == {"from": None, "to": "Net 60"}


async def test_contacts_keep_exactly_one_primary(client):
    o = await org(client)
    s = await create_supplier(client, o.buyer, "Northwind Metals")
    first = s["contacts"][0]
    second = (await client.post(f"/suppliers/{s['id']}/contacts", json={"name": "Bob", "email": "Bob@NW.test", "is_primary": True}, headers=o.buyer.headers)).json()
    assert second["is_primary"] is True and second["email"] == "bob@nw.test"
    contacts = {c["id"]: c for c in (await client.get(f"/suppliers/{s['id']}", headers=o.buyer.headers)).json()["contacts"]}
    assert contacts[first["id"]]["is_primary"] is False
    res = await client.patch(f"/suppliers/{s['id']}/contacts/{first['id']}", json={"is_primary": True}, headers=o.buyer.headers)
    assert res.status_code == 200 and res.json()["is_primary"] is True
    assert (await client.patch(f"/suppliers/{s['id']}/contacts/{first['id']}", json={"is_primary": False}, headers=o.buyer.headers)).status_code == 400
    dup = await client.post(f"/suppliers/{s['id']}/contacts", json={"name": "x", "email": "bob@nw.test"}, headers=o.buyer.headers)
    assert dup.status_code == 409
    assert (await client.delete(f"/suppliers/{s['id']}/contacts/{first['id']}", headers=o.buyer.headers)).status_code == 200
    left = (await client.get(f"/suppliers/{s['id']}", headers=o.buyer.headers)).json()["contacts"]
    assert len(left) == 1 and left[0]["is_primary"] is True


# --- Qualification -----------------------------------------------------------------------------------------------
async def test_default_criteria_are_seeded_once(client):
    o = await org(client)
    crit = await criteria(client, o.buyer)
    assert {"quality_certification", "sanctions_screening", "code_of_conduct"} <= {k for k, c in crit.items() if c["required"]}
    assert len(await criteria(client, o.auditor)) == len(crit)


async def test_full_qualification_workflow(client):
    o = await org(client)
    s = await create_supplier(client, o.buyer, "Northwind Metals")
    crit = await criteria(client, o.buyer)
    assert (await client.post(f"/suppliers/{s['id']}/qualify", json={}, headers=o.approver.headers)).status_code == 409
    assert (await client.post(f"/suppliers/{s['id']}/submit", headers=o.buyer.headers)).json()["status"] == "under_review"
    res = await client.post(f"/suppliers/{s['id']}/qualify", json={}, headers=o.approver.headers)
    assert res.status_code == 409 and res.json()["error"]["code"] == "required_checks_missing"
    for key in ("quality_certification", "sanctions_screening"):
        await client.put(f"/suppliers/{s['id']}/qualification/{crit[key]['id']}", json={"status": "passed"}, headers=o.buyer.headers)
    waive = await client.put(f"/suppliers/{s['id']}/qualification/{crit['code_of_conduct']['id']}", json={"status": "waived", "note": "Group policy covers it"}, headers=o.buyer.headers)
    assert waive.status_code == 403 and waive.json()["error"]["code"] == "waiver_not_allowed"
    short = await client.put(f"/suppliers/{s['id']}/qualification/{crit['code_of_conduct']['id']}", json={"status": "waived", "note": "ok"}, headers=o.approver.headers)
    assert short.status_code == 403  # approvers cannot record checks (suppliers:manage), only qualify
    res = await client.put(f"/suppliers/{s['id']}/qualification/{crit['code_of_conduct']['id']}", json={"status": "waived", "note": "Group-wide code of conduct already signed"}, headers=o.admin.headers)
    assert res.status_code == 200 and res.json()["summary"]["ready"] is True
    q = await client.post(f"/suppliers/{s['id']}/qualify", json={"note": "Approved for brackets"}, headers=o.approver.headers)
    assert q.status_code == 200 and q.json()["status"] == "qualified" and q.json()["qualification"]["qualified"] is True
    assert q.json()["decided_by"] == o.approver.user_id
    detail = (await client.get(f"/suppliers/{s['id']}/qualification", headers=o.auditor.headers)).json()
    statuses = {c["criterion"]["key"]: c["effective_status"] for c in detail["checks"]}
    assert statuses["code_of_conduct"] == "waived" and statuses["insurance"] == "pending"


async def test_check_rules(client):
    o = await org(client)
    s = await create_supplier(client, o.buyer, "Northwind Metals")
    crit = (await criteria(client, o.buyer))["insurance"]
    url = f"/suppliers/{s['id']}/qualification/{crit['id']}"
    assert (await client.put(url, json={"status": "failed"}, headers=o.buyer.headers)).json()["error"]["code"] == "note_required"
    assert (await client.put(url, json={"status": "pending", "expires_at": "2030-01-01"}, headers=o.buyer.headers)).json()["error"]["code"] == "expiry_not_applicable"
    past = (date.today() - timedelta(days=1)).isoformat()
    assert (await client.put(url, json={"status": "passed", "expires_at": past}, headers=o.buyer.headers)).json()["error"]["code"] == "already_expired"
    assert (await client.put(f"/suppliers/{s['id']}/qualification/{uuid.uuid4()}", json={"status": "passed"}, headers=o.buyer.headers)).status_code == 404


async def test_expired_evidence_drops_qualification(client, admin_engine):
    o = await org(client)
    s = await create_supplier(client, o.buyer, "Northwind Metals")
    await qualify(client, o, s["id"])
    async with admin_engine.begin() as conn:
        await conn.execute(text(
            "UPDATE supplier_qualification_checks SET expires_at = current_date - 1 WHERE supplier_id = CAST(:s AS uuid) "
            "AND criterion_id = (SELECT id FROM qualification_criteria WHERE key = 'quality_certification' AND organization_id = supplier_qualification_checks.organization_id)"
        ), {"s": s["id"]})
    detail = (await client.get(f"/suppliers/{s['id']}", headers=o.buyer.headers)).json()
    assert detail["status"] == "qualified" and detail["qualification"]["qualified"] is False
    assert detail["qualification"]["expired"] and detail["qualification"]["missing_required"]


async def test_failing_a_required_check_sends_a_qualified_supplier_back_to_review(client):
    o = await org(client)
    s = await create_supplier(client, o.buyer, "Northwind Metals")
    await qualify(client, o, s["id"])
    crit = (await criteria(client, o.buyer))["sanctions_screening"]
    res = await client.put(f"/suppliers/{s['id']}/qualification/{crit['id']}", json={"status": "failed", "note": "Listed entity"}, headers=o.buyer.headers)
    assert res.json()["status"] == "under_review"
    actions = [e["action"] for e in (await client.get(f"/suppliers/{s['id']}/events", headers=o.buyer.headers)).json()]
    assert actions[0] == "auto_requalification"


async def test_segregation_of_duties_on_qualification(client, monkeypatch):
    o = await org(client)
    s = await create_supplier(client, o.admin, "Northwind Metals")
    for c in (await criteria(client, o.admin)).values():
        if c["required"]:
            await client.put(f"/suppliers/{s['id']}/qualification/{c['id']}", json={"status": "passed"}, headers=o.admin.headers)
    await client.post(f"/suppliers/{s['id']}/submit", headers=o.admin.headers)
    res = await client.post(f"/suppliers/{s['id']}/qualify", json={}, headers=o.admin.headers)
    assert res.status_code == 403 and res.json()["error"]["code"] == "segregation_of_duties"
    monkeypatch.setattr(settings, "require_separate_approver", False)
    assert (await client.post(f"/suppliers/{s['id']}/qualify", json={}, headers=o.admin.headers)).status_code == 200


async def test_block_disqualify_reopen(client):
    o = await org(client)
    s = await create_supplier(client, o.buyer, "Northwind Metals")
    await qualify(client, o, s["id"])
    assert (await client.post(f"/suppliers/{s['id']}/block", json={"reason": "x"}, headers=o.approver.headers)).status_code == 422
    blocked = await client.post(f"/suppliers/{s['id']}/block", json={"reason": "Quality escape on PO 991"}, headers=o.approver.headers)
    assert blocked.json()["status"] == "blocked" and blocked.json()["qualification"]["qualified"] is False
    crit = (await criteria(client, o.buyer))["insurance"]
    assert (await client.put(f"/suppliers/{s['id']}/qualification/{crit['id']}", json={"status": "passed"}, headers=o.buyer.headers)).status_code == 409
    assert (await client.post(f"/suppliers/{s['id']}/reopen", json={}, headers=o.approver.headers)).json()["status"] == "prospect"
    await client.post(f"/suppliers/{s['id']}/submit", headers=o.buyer.headers)
    res = await client.post(f"/suppliers/{s['id']}/disqualify", json={"reason": "Capacity too small"}, headers=o.approver.headers)
    assert res.json()["status"] == "disqualified"


@pytest.mark.parametrize("role,can_manage,can_qualify", [
    ("requester", False, False), ("buyer", True, False), ("approver", False, True), ("auditor", False, False),
])
async def test_supplier_permissions(client, role, can_manage, can_qualify):
    o = await org(client)
    who = getattr(o, role)
    s = await create_supplier(client, o.admin, "Northwind Metals")
    assert (await client.get("/suppliers", headers=who.headers)).status_code == 200
    assert (await client.get(f"/suppliers/{s['id']}", headers=who.headers)).status_code == 200
    res = await client.post("/suppliers", json={"name": f"New {role}"}, headers=who.headers)
    assert res.status_code == (201 if can_manage else 403)
    res = await client.patch(f"/suppliers/{s['id']}", json={"notes": "x"}, headers=who.headers)
    assert res.status_code == (200 if can_manage else 403)
    res = await client.post(f"/suppliers/{s['id']}/block", json={"reason": "testing roles"}, headers=who.headers)
    assert res.status_code == (200 if can_qualify else 403)


async def test_supplier_export_csv_is_safe(client):
    o = await org(client)
    await create_supplier(client, o.buyer, "=cmd|' /C calc'!A0", email="x@evil.test")
    await create_supplier(client, o.buyer, "Northwind Metals")
    res = await client.get("/suppliers/export", headers=o.auditor.headers)
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/csv")
    body = res.content.decode("utf-8-sig")
    assert "'=cmd" in body and "\n=cmd" not in body and "SUP-0002,Northwind Metals,prospect,False,0" in body
    actions = [r["action"] for r in (await client.get("/audit-logs", headers=o.admin.headers)).json()]
    assert "supplier.exported" in actions


async def test_suppliers_are_isolated_between_organizations(client):
    a = await org(client)
    b = await org(client, email="admin@beta.test", name="Beta", domain="beta.test")
    s = await create_supplier(client, a.buyer, "Northwind Metals")
    for method, path, body in [
        ("GET", f"/suppliers/{s['id']}", None), ("PATCH", f"/suppliers/{s['id']}", {"notes": "x"}),
        ("GET", f"/suppliers/{s['id']}/qualification", None), ("POST", f"/suppliers/{s['id']}/submit", None),
        ("POST", f"/suppliers/{s['id']}/contacts", {"name": "x", "email": "x@x.test"}),
        ("GET", f"/suppliers/{s['id']}/events", None),
    ]:
        res = await client.request(method, path, json=body, headers=b.buyer.headers)
        assert res.status_code == 404, (method, path)
    assert (await client.get("/suppliers", headers=b.buyer.headers)).json() == {"items": [], "total": 0}
    same = await create_supplier(client, b.buyer, "Northwind Metals")  # names are unique per organization only
    assert same["code"] == "SUP-0001"
