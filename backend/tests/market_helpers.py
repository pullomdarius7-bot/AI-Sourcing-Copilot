"""Shared set-up for marketplace tests: a supplier with a published lot and buyers."""
from datetime import date, timedelta

from tests.helpers import register_admin

SELLER_ORG = "Pallet Kings Liquidation"
SELLER_EMAIL = "owner@palletkings.test"

MANIFEST = """Lot 4471 manifest - warehouse export
Item Description,Brand,Dept,Grade,UPC,Qty,Unit Retail,Ext. Retail
DEWALT 20V MAX Cordless Drill Kit DCD771C2,DeWalt,Tools,Customer Return,885911425308,4,$129.00,$516.00
Ninja AF101 Air Fryer 4 Qt,,Kitchen,LIKE NEW,622356548329,10,99.99,999.90
Apple AirPods Pro 2nd Gen B0CHWRXH8B,Apple,Electronics,Salvage - for parts,194253397168,6,249.00,1494.00
Nike Air Max 90 Mens Size 10,Nike,Shoes,New,,12,130,1560
Total,,,,,32,,4569.90
"""

LISTING = {
    "title": "Mixed tools, kitchen and electronics returns",
    "lot_type": "single_pallet",
    "asking_price": "1200.00",
    "dispatch_zip": "60607",
    "liftgate_required": True,
    "reserve_price": "900.00",
    "pickup_address": "1200 W Fulton Market, Dock 7, Chicago IL 60607",
    "pickup_contact": "Dock supervisor Rosa",
    "pickup_phone": "312-555-0142",
}


async def supplier(client, email=SELLER_EMAIL, org=SELLER_ORG):
    return await register_admin(client, email=email, org=org, account_type="supplier")


async def buyer(client, email="buyer@flipco.test", org="FlipCo Resale"):
    return await register_admin(client, email=email, org=org, account_type="solo_buyer")


async def upload(client, worker, account, data: str | bytes = MANIFEST, name="manifest.csv", content_type="text/csv",
                 default_condition: str | None = None):
    payload = data.encode() if isinstance(data, str) else data
    form = {"default_condition": default_condition} if default_condition else {}
    res = await client.post("/supplier/manifests", files={"file": (name, payload, content_type)}, data=form,
                            headers=account.headers)
    assert res.status_code == 201, res.text
    await worker.drain()
    lot = await client.get(f"/supplier/lots/{res.json()['id']}", headers=account.headers)
    assert lot.status_code == 200, lot.text
    return lot.json()


async def publish(client, account, lot_id, **overrides):
    res = await client.patch(f"/supplier/lots/{lot_id}", json={**LISTING, **overrides}, headers=account.headers)
    assert res.status_code == 200, res.text
    res = await client.post(f"/supplier/lots/{lot_id}/publish", headers=account.headers)
    assert res.status_code == 200, res.text
    return res.json()


async def listed_lot(client, worker, seller=None, **overrides):
    seller = seller or await supplier(client)
    lot = await upload(client, worker, seller)
    await publish(client, seller, lot["id"], **overrides)
    return seller, lot["id"]


def offer(price="1000.00", **extra):
    return {"offered_price": price, "shipping_terms": "buyer_freight",
            "target_close_date": (date.today() + timedelta(days=10)).isoformat(), **extra}
