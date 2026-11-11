"""Fixtures for the comparison engine and RFQ documents."""
import copy

ITEMS = [
    {"id": "i1", "position": 0, "statement": "Stainless steel bracket AISI 304, 3 mm", "quantity": 5000, "unit": "pcs", "priority": "mandatory"},
    {"id": "i2", "position": 1, "statement": "Brushed surface finish", "quantity": 5000, "unit": "pcs", "priority": "preferred"},
    {"id": "i3", "position": 2, "statement": "ISO 9001 certificate copy", "quantity": None, "unit": None, "priority": "mandatory"},
]
SUPPLIERS = [
    {"id": "s1", "name": "Northwind Metals", "code": "SUP-0001", "qualified": True},
    {"id": "s2", "name": "Kessler GmbH", "code": "SUP-0002", "qualified": True},
    {"id": "s3", "name": "Orion Cast", "code": "SUP-0003", "qualified": False},
]


def line(item, unit_price=None, total=None, qty=None, unit="pcs", lead=None, match="confirmed", confidence=1.0, moq=None):
    return {"rfq_item_id": item, "match_status": match, "unit_price": unit_price, "total_price": total,
            "quantity": qty, "unit": unit, "lead_time_days": lead, "confidence": confidence, "description": item, "moq": moq}


def quote(sid, lines, status="verified", currency="EUR", incoterms="DAP", payment="Net 30", valid="2026-12-31"):
    return {"id": f"q-{sid}", "supplier_id": sid, "status": status, "currency": currency, "incoterms": incoterms,
            "payment_terms": payment, "valid_until": valid, "lines": lines}


def base_inputs():
    return {
        "currency": "EUR",
        "as_of": "2026-11-10",
        "items": copy.deepcopy(ITEMS),
        "suppliers": copy.deepcopy(SUPPLIERS),
        "quotes": [
            quote("s1", [line("i1", 4.20, qty=5000, lead=21), line("i2", 0.30, qty=5000, lead=21), line("i3", 0, unit=None, lead=0)]),
            quote("s2", [line("i1", 4.00, qty=5000, lead=35), line("i2", 0.40, qty=5000, lead=35)]),
            quote("s3", [line("i1", 3.90, qty=5000, lead=14), line("i2", 0.25, qty=5000, lead=14), line("i3", 10, unit=None)],
                  incoterms="FOB"),
        ],
    }


DOCUMENT = {
    "schema": "ai-sourcing-copilot/rfq@1", "number": "RFQ-2026-0001", "title": "Stainless brackets – 3 mm",
    "version": 2, "currency": "EUR", "response_due_date": "2026-12-01",
    "message": "Please quote for the attached requirements.\nPartial quotes are welcome.",
    "organization": "Meridian Components Ltd", "category": "Sheet metal",
    "items": [
        {"line": 1, "statement": "Deliver 5,000 pcs of stainless steel brackets <AISI 304> & co", "category": "material",
         "priority": "mandatory", "quantity": 5000, "unit": "pcs",
         "parameters": [{"name": "material", "value": "AISI 304", "unit": None, "qualifier": None, "unknown": False},
                        {"name": "thickness", "value": 3, "unit": "mm", "qualifier": "min", "unknown": False},
                        {"name": "grade_class", "value": None, "unit": None, "qualifier": None, "unknown": True}]},
        {"line": 2, "statement": "Supplier to state: warranty period", "category": "other", "priority": "unknown",
         "quantity": None, "unit": None,
         "parameters": [{"name": "warranty_period", "value": None, "unit": None, "qualifier": None, "unknown": True}]},
    ],
}
