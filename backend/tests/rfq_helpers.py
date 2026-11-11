from datetime import date, timedelta

from tests.helpers import add_member, register_admin

DUE = (date.today() + timedelta(days=30)).isoformat()


class Org:
    pass


async def org(client, email="admin@acme.test", name="Acme Corp", domain="acme.test"):
    """Admin, buyer (manages suppliers/RFQs/quotes), approver (qualifies, approves, awards), auditor, requester."""
    o = Org()
    o.admin = await register_admin(client, email=email, org=name)
    o.buyer = await add_member(client, o.admin, f"buyer@{domain}", "buyer")
    o.approver = await add_member(client, o.admin, f"approver@{domain}", "approver")
    o.auditor = await add_member(client, o.admin, f"auditor@{domain}", "auditor")
    o.requester = await add_member(client, o.admin, f"requester@{domain}", "requester")
    return o


async def criteria(client, who):
    res = await client.get("/qualification-criteria", headers=who.headers)
    assert res.status_code == 200, res.text
    return {c["key"]: c for c in res.json()}


async def create_supplier(client, who, name, *, email=None, category_ids=(), **fields):
    body = {"name": name, "category_ids": list(category_ids), **fields}
    if email is not False:
        body["contacts"] = [{"name": f"{name} Sales", "email": email or f"sales@{name.lower().replace(' ', '')}.test"}]
    res = await client.post("/suppliers", json=body, headers=who.headers)
    assert res.status_code == 201, res.text
    return res.json()


async def qualify(client, o, supplier_id):
    crit = await criteria(client, o.buyer)
    for c in crit.values():
        if c["required"]:
            res = await client.put(f"/suppliers/{supplier_id}/qualification/{c['id']}", json={"status": "passed"}, headers=o.buyer.headers)
            assert res.status_code == 200, res.text
    assert (await client.post(f"/suppliers/{supplier_id}/submit", headers=o.buyer.headers)).status_code == 200
    res = await client.post(f"/suppliers/{supplier_id}/qualify", json={"note": "All checks passed"}, headers=o.approver.headers)
    assert res.status_code == 200, res.text
    return res.json()


async def qualified_supplier(client, o, name, **kw):
    s = await create_supplier(client, o.buyer, name, **kw)
    return await qualify(client, o, s["id"])


ITEMS = [
    {"statement": "Stainless steel bracket AISI 304, 3 mm thick", "category": "material", "priority": "mandatory",
     "quantity": 5000, "unit": "pcs", "parameters": [{"name": "material", "value": "AISI 304"},
                                                      {"name": "grade_class", "unknown": True}]},
    {"statement": "Brushed surface finish", "category": "quality", "priority": "preferred", "quantity": 5000, "unit": "pcs"},
]


async def draft_rfq(client, o, suppliers=(), items=ITEMS, **fields):
    body = {"title": "Stainless brackets", "response_due_date": DUE, "currency": "EUR", **fields}
    res = await client.post("/rfqs", json=body, headers=o.buyer.headers)
    assert res.status_code == 201, res.text
    rfq = res.json()
    for item in items:
        res = await client.post(f"/rfqs/{rfq['id']}/items", json=item, headers=o.buyer.headers)
        assert res.status_code == 201, res.text
        rfq = res.json()
    if suppliers:
        res = await client.post(f"/rfqs/{rfq['id']}/suppliers", json={"supplier_ids": [s["id"] for s in suppliers]}, headers=o.buyer.headers)
        assert res.status_code == 200, res.text
        rfq = res.json()
    return rfq


async def approved_rfq(client, o, suppliers, **fields):
    rfq = await draft_rfq(client, o, suppliers, **fields)
    res = await client.post(f"/rfqs/{rfq['id']}/submit", headers=o.buyer.headers)
    assert res.status_code == 200, res.text
    res = await client.post(f"/rfqs/{rfq['id']}/approve", json={"note": "OK to send"}, headers=o.approver.headers)
    assert res.status_code == 200, res.text
    return res.json()


async def sent_rfq(client, worker, o, suppliers, **fields):
    rfq = await approved_rfq(client, o, suppliers, **fields)
    res = await client.post(f"/rfqs/{rfq['id']}/send", headers=o.buyer.headers)
    assert res.status_code == 200, res.text
    await worker.drain()
    return (await client.get(f"/rfqs/{rfq['id']}", headers=o.buyer.headers)).json()


def line_for(rfq, n, **values):
    return {"rfq_item_id": rfq["items"][n]["id"], "description": rfq["items"][n]["statement"], **values}


async def verified_quote(client, o, rfq, supplier, lines, **header):
    body = {"supplier_id": supplier["id"], "currency": "EUR", "incoterms": "DAP", "payment_terms": "Net 30",
            "valid_until": DUE, "lines": lines, **header}
    res = await client.post(f"/rfqs/{rfq['id']}/quotes", json=body, headers=o.buyer.headers)
    assert res.status_code == 201, res.text
    res = await client.post(f"/quotes/{res.json()['id']}/verify", headers=o.buyer.headers)
    assert res.status_code == 200, res.text
    return res.json()
