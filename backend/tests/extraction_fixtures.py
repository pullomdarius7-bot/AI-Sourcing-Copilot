"""Extraction fixtures: a realistic RFQ plus scripted model outputs (good, hallucinated, conflicting)."""
RFQ_TEXT = """REQUEST FOR QUOTATION RFQ-2026-014
Buyer: Meridian Components Ltd

1. Scope
The supplier shall deliver 5,000 pcs of stainless steel brackets, material AISI 304.
Brackets must have a thickness of 3 mm with a tolerance of \u00b10.1 mm.
Surface finish should preferably be brushed.

2. Delivery
Delivery is required by 2026-09-30 to our warehouse in Rotterdam, Incoterms DAP.
Packaging may be pallets or cartons, approximately 500 pcs per carton.

3. Quality and commercial
Supplier must hold ISO 9001 certification.
Payment terms: Net 45 days.
"""

L_SCOPE = "The supplier shall deliver 5,000 pcs of stainless steel brackets, material AISI 304."
L_DIM = "Brackets must have a thickness of 3 mm with a tolerance of \u00b10.1 mm."
L_FINISH = "Surface finish should preferably be brushed."
L_DELIVERY = "Delivery is required by 2026-09-30 to our warehouse in Rotterdam, Incoterms DAP."
L_PACK = "Packaging may be pallets or cartons, approximately 500 pcs per carton."
L_ISO = "Supplier must hold ISO 9001 certification."
L_PAY = "Payment terms: Net 45 days."


def rp(name, value_type="text", *, text=None, number=None, unit=None, qualifier=None,
       unknown=False, confidence=0.9, source=None):
    return {
        "name": name, "value_type": value_type, "text": text, "number": number, "unit": unit,
        "qualifier": qualifier, "unknown": unknown, "confidence": confidence, "source": source,
    }


def rq(statement, source, category, priority, params, confidence=0.9):
    return {
        "statement": statement, "source": source, "category": category, "priority": priority,
        "confidence": confidence, "parameters": params,
    }


def extraction(requirements, unspecified=()):
    return {"requirements": list(requirements), "unspecified": [dict(u) for u in unspecified]}


def good_extraction():
    return extraction(
        [
            rq("Deliver 5,000 pcs of stainless steel brackets in AISI 304", L_SCOPE, "material", "mandatory", [
                rp("quantity", "number", number=5000, unit="pcs", source="deliver 5,000 pcs", qualifier="exact"),
                rp("material", text="AISI 304", source="material AISI 304"),
                rp("grade_class", unknown=True, confidence=0.0),
            ]),
            rq("Brackets must have a thickness of 3 mm with a tolerance of \u00b10.1 mm", L_DIM, "dimensions", "mandatory", [
                rp("thickness", "number", number=3, unit="mm", source="thickness of 3 mm"),
                rp("tolerance", "number", number=0.1, unit="mm", source="tolerance of \u00b10.1 mm"),
            ]),
            rq("Surface finish should preferably be brushed", L_FINISH, "quality", "preferred", [
                rp("surface_finish", text="brushed", source=L_FINISH),
            ], confidence=0.8),
            rq("Delivery required by 2026-09-30 to Rotterdam, Incoterms DAP", L_DELIVERY, "delivery", "mandatory", [
                rp("delivery_date", text="2026-09-30", source="required by 2026-09-30"),
                rp("delivery_location", text="Rotterdam", source="warehouse in Rotterdam"),
                rp("incoterms", text="DAP", source="Incoterms DAP"),
            ]),
            rq("Supplier must hold ISO 9001 certification", L_ISO, "compliance", "mandatory", [
                rp("certification", text="ISO 9001", source=L_ISO),
            ]),
            rq("Payment terms: Net 45 days", L_PAY, "commercial", "unknown", [
                rp("payment_terms", text="Net 45 days", source=L_PAY),
            ]),
        ],
        unspecified=[{"name": "warranty_period", "reason": "The document does not state a warranty period"}],
    )


def hallucinated_extraction():
    """Every kind of unsupported claim a model can make. All of it must end up unknown, dropped or corrected."""
    return extraction(
        [
            # invented requirement (quote is not in the document)
            rq("Provide a five year warranty", "The supplier shall provide a 5 year warranty.", "commercial", "mandatory", [
                rp("warranty_period", "number", number=5, unit="years", source="5 year warranty"),
            ]),
            rq("Deliver 6,000 gold plated brackets", L_SCOPE, "material", "mandatory", [
                rp("material", text="AISI 316", source="material AISI 304"),            # value not in its quote
                rp("quantity", "number", number=6000, unit="pcs", source="deliver 5,000 pcs"),  # wrong number
                rp("coating", text="gold plating", source=None),                          # no source at all
                rp("finish", text="mirror", source="mirror finish required"),             # invented quote
            ]),
            rq("Brackets must be preferably brushed", L_FINISH, "quality", "mandatory", [  # priority not supported
                rp("surface_finish", text="brushed", source="preferably be brushed"),
            ]),
            rq("Weight of 12 kg per bracket", L_DIM, "dimensions", "unknown", [              # paraphrase invents numbers
                rp("thickness", "number", number=3, unit="kg", source="thickness of 3 mm"),    # unit not in quote
            ]),
        ],
        unspecified=[{"name": "material", "reason": "claimed missing but present"}, {"name": "incoterms", "reason": "Not stated"}],
    )


CONFLICT_TEXT = """Terms of delivery
Delivery shall be made under Incoterms DAP Rotterdam.
Annex B: all shipments are Incoterms FOB Shanghai.
"""


def conflicting_extraction():
    return extraction([
        rq("Delivery under Incoterms DAP", "Delivery shall be made under Incoterms DAP Rotterdam.", "delivery", "mandatory", [
            rp("incoterms", text="DAP", source="Incoterms DAP"),
        ]),
        rq("Shipments are Incoterms FOB", "Annex B: all shipments are Incoterms FOB Shanghai.", "delivery", "unknown", [
            rp("incoterms", text="FOB", source="Incoterms FOB"),
        ]),
    ])


SHEET_TEXT = """## Sheet: Specs
Item\tValue
Material\tAluminium 6061
## Sheet: Commercial
Quantity\t250 pcs
"""
