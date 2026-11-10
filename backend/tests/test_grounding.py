import pytest

from app.ai.guardrails import OutputInvalid
from app.requirements import grounding as gr
from app.requirements import service as svc
from app.requirements.schemas import EXTRACTION_JSON_SCHEMA, validate_raw
from tests import extraction_fixtures as fx


def grounded(raw, text=fx.RFQ_TEXT):
    index = gr.TextIndex(text)
    reqs, unspecified, flags = gr.ground_chunk(validate_raw(raw), index, 0)
    return gr.finalize(reqs, unspecified, flags)


def params(result):
    return {(r.statement[:12], p.name): p for r in result.requirements for p in r.params}


def by_name(result, name):
    return [p for r in result.requirements for p in r.params if p.name == name]


# --- Locating quotes / evidence offsets ------------------------------------------------------
def test_exact_quote_offsets_slice_the_document():
    index = gr.TextIndex(fx.RFQ_TEXT)
    hit = index.locate(fx.L_ISO)
    assert hit.method == "exact" and fx.RFQ_TEXT[hit.start : hit.end] == fx.L_ISO


def test_normalized_quote_still_maps_back_to_original_offsets():
    text = "Intro line.\nThe  supplier\u00a0shall deliver \u201c5,000\u201d pcs \u2013 stainless\nsteel.\nEnd."
    index = gr.TextIndex(text)
    hit = index.locate('The supplier shall deliver "5,000" pcs - stainless steel.')
    assert hit.method == "normalized"
    assert gr.normalize(text[hit.start : hit.end]) == 'the supplier shall deliver "5,000" pcs - stainless steel.'
    assert text[hit.start : hit.start + 3] == "The" and text[hit.end - 1] == "."


def test_missing_or_tiny_quotes_are_not_located():
    index = gr.TextIndex(fx.RFQ_TEXT)
    assert index.locate("The supplier shall provide a 5 year warranty.") is None
    assert index.locate("ab") is None and index.locate("   ") is None


def test_hint_prefers_later_occurrence_but_falls_back():
    text = "pay net 30\n" + "x" * 50 + "\npay net 30\n"
    index = gr.TextIndex(text)
    assert index.locate("pay net 30", 0).start == 0
    assert index.locate("pay net 30", 20).start > 50
    assert index.locate("pay net 30", len(text) - 2).start == 0  # nothing after the hint: fall back


def test_line_and_sheet_context():
    index = gr.TextIndex(fx.SHEET_TEXT)
    hit = index.locate("Quantity\t250 pcs")
    assert index.section_of(hit.start) == "Commercial" and index.line_of(hit.start) == 5
    assert index.section_of(index.locate("Aluminium 6061").start) == "Specs"
    assert gr.TextIndex("no sheets here").section_of(3) is None


# --- Happy path ---------------------------------------------------------------------------------
def test_good_extraction_is_fully_grounded():
    result = grounded(fx.good_extraction())
    assert len(result.requirements) == 6
    for req in result.requirements:
        assert fx.RFQ_TEXT[req.start : req.end] == req.quote and 0 < req.confidence <= 1
        for p in req.params:
            if p.unknown:
                assert (p.text, p.number, p.quote, p.start, p.confidence, p.grounding) == (None, None, None, None, 0.0, "unknown")
            else:
                assert p.grounding == "grounded" and p.quote and fx.RFQ_TEXT[p.start : p.end] == p.quote
                assert 0 < p.confidence <= p.model_confidence <= 1
    qty = by_name(result, "quantity")[0]
    assert (qty.number, qty.unit, qty.qualifier) == (5000, "pcs", "exact")
    tol = by_name(result, "tolerance")[0]
    assert (tol.number, tol.unit) == (0.1, "mm") and "\u00b10.1" in tol.quote
    assert by_name(result, "incoterms")[0].text == "DAP"


def test_unknown_stays_unknown_and_gaps_become_explicit_unknowns():
    result = grounded(fx.good_extraction())
    grade = by_name(result, "grade_class")[0]
    assert grade.unknown and grade.detail["reason"] == "Not stated in the document"
    assert [u.name for u in result.unknowns] == ["warranty_period"]
    w = result.unknowns[0]
    assert w.unknown and w.text is None and w.number is None and w.quote is None and w.confidence == 0.0


def test_priority_keywords_support_the_claim():
    result = grounded(fx.good_extraction())
    assert [r.priority for r in result.requirements] == ["mandatory", "mandatory", "preferred", "mandatory", "mandatory", "unknown"]
    assert not any("priority_not_supported" in r.flags for r in result.requirements)


def test_hedged_wording_lowers_confidence():
    result = grounded(fx.good_extraction())
    finish = by_name(result, "surface_finish")[0]
    cert = by_name(result, "certification")[0]
    assert "hedged_language" in finish.flags and finish.detail["hedging"] == 0.7
    assert finish.confidence < cert.confidence


# --- Hallucination defences ----------------------------------------------------------------------------
def test_invented_requirement_is_dropped_and_reported():
    result = grounded(fx.hallucinated_extraction())
    assert all("warranty" not in r.statement.lower() for r in result.requirements)
    assert {"code": "dropped_requirement", "reason": "source_not_found", "statement": "Provide a five year warranty"} in result.flags


def test_invented_values_become_unknown_and_never_keep_the_claim_as_a_value():
    result = grounded(fx.hallucinated_extraction())
    material = by_name(result, "material")[0]
    assert material.unknown and material.text is None and "value_not_in_source" in material.flags
    assert material.detail["discarded_model_claim"]["text"] == "AISI 316"  # kept for audit only
    qty = by_name(result, "quantity")[0]
    assert qty.unknown and qty.number is None and "value_not_in_source" in qty.flags
    assert by_name(result, "coating")[0].unknown
    finish = by_name(result, "finish")[0]
    assert finish.unknown and "ungrounded_source" in finish.flags
    for p in (material, qty, finish):
        assert p.quote is None and p.start is None and p.confidence == 0.0


def test_unsupported_unit_is_dropped_but_flagged():
    result = grounded(fx.hallucinated_extraction())
    thickness = by_name(result, "thickness")[0]
    assert thickness.number == 3 and thickness.unit is None and "unit_not_in_source" in thickness.flags
    assert thickness.detail["unit"] == 0.85


def test_unsupported_priority_becomes_unknown():
    result = grounded(fx.hallucinated_extraction())
    req = next(r for r in result.requirements if r.quote == fx.L_FINISH)
    assert req.priority == "unknown" and "priority_not_supported" in req.flags


def test_unsupported_paraphrase_is_replaced_by_the_verbatim_quote():
    result = grounded(fx.hallucinated_extraction())
    req = next(r for r in result.requirements if r.quote == fx.L_DIM)
    assert "kg" not in req.statement and req.statement == fx.L_DIM
    assert "statement_replaced_with_quote" in req.flags
    gold = next(r for r in result.requirements if r.quote == fx.L_SCOPE)
    assert "gold" not in gold.statement.lower() and "6,000" not in gold.statement


def test_unspecified_claim_is_dropped_when_the_value_was_actually_found():
    raw = fx.good_extraction()
    raw["unspecified"].append({"name": "incoterms", "reason": "claimed missing"})
    result = grounded(raw)
    assert "incoterms" not in [u.name for u in result.unknowns]
    assert {"code": "unspecified_but_found", "name": "incoterms"} in result.flags


def test_parameter_outside_requirement_quote_is_flagged():
    raw = fx.extraction([fx.rq("Material is AISI 304", fx.L_FINISH, "material", "unknown", [
        fx.rp("material", text="AISI 304", source="material AISI 304"),
    ])])
    p = by_name(grounded(raw), "material")[0]
    assert not p.unknown and "outside_requirement_quote" in p.flags and p.detail["context"] == 0.9


def test_conflicting_singleton_values_are_capped_and_flagged():
    result = grounded(fx.conflicting_extraction(), fx.CONFLICT_TEXT)
    values = by_name(result, "incoterms")
    assert {p.text for p in values} == {"DAP", "FOB"}
    assert all(p.confidence <= 0.5 and "conflicting_values" in p.flags for p in values)
    assert {"code": "conflicting_values", "name": "incoterms"} in result.flags


def test_duplicate_requirements_and_parameters_are_merged():
    raw = fx.good_extraction()
    raw["requirements"] += raw["requirements"][:2]
    result = grounded(raw)
    assert len(result.requirements) == 6
    assert sum(1 for f in result.flags if f["code"] == "duplicate_requirement") == 2


def test_empty_extraction_is_reported_not_invented():
    result = grounded(fx.extraction([]))
    assert result.requirements == [] and result.unknowns == [] and {"code": "no_requirements_found"} in result.flags


# --- Scoring ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("model", [0.0, 0.3, 0.7, 0.95, 1.0])
@pytest.mark.parametrize("method", ["exact", "normalized"])
def test_confidence_never_exceeds_the_models_own(model, method):
    final, detail, _ = gr.score(model, method, "a perfectly clear quote of reasonable length")
    assert final <= model and detail["final"] == final


def test_scoring_factors():
    exact, _, _ = gr.score(0.9, "exact", "Supplier must hold ISO 9001 certification.")
    normalized, _, _ = gr.score(0.9, "normalized", "Supplier must hold ISO 9001 certification.")
    hedged, _, flags = gr.score(0.9, "exact", "approximately 500 pcs")
    short, _, short_flags = gr.score(0.9, "exact", "5 mm")
    assert exact == 0.9 and normalized == 0.855 and hedged == 0.63 and "hedged_language" in flags
    assert short == 0.72 and "short_quote" in short_flags
    assert gr.score(5.0, "exact", "a long enough quote here")[0] == 1.0  # out-of-range model confidence is clamped
    assert [gr.bucket(x) for x in (0.9, 0.85, 0.7, 0.6, 0.59)] == ["high", "high", "medium", "medium", "low"]


@pytest.mark.parametrize(
    "statement,ok",
    [
        ("Deliver 5,000 pcs of stainless steel brackets", True),
        ("Deliver 6,000 pcs of stainless steel brackets", False),
        ("Provide gold plated titanium brackets", False),
        ("Brackets thickness 3 mm", True),
    ],
)
def test_statement_support(statement, ok):
    assert gr.statement_supported(statement, fx.L_SCOPE + " " + fx.L_DIM) is ok


def test_priority_validation():
    assert gr.validate_priority("mandatory", "the supplier shall deliver") == ("mandatory", None)
    assert gr.validate_priority("mandatory", "ideally brushed") == ("unknown", "priority_not_supported")
    assert gr.validate_priority("preferred", "should be brushed")[0] == "preferred"
    assert gr.validate_priority("optional", "packaging may be pallets")[0] == "optional"
    assert gr.validate_priority("unknown", "whatever")[0] == "unknown"


# --- Chunking, schema -----------------------------------------------------------------------------------------
def test_chunks_cover_the_text_with_overlap_and_respect_the_cap():
    text = "\n".join(f"line {i} " + "x" * 40 for i in range(200))
    chunks = svc.chunk_text(text, 1000, 100, 50)
    assert chunks[0][0] == 0 and chunks[-1][1] == len(text)
    assert all(b[0] < a[1] for a, b in zip(chunks, chunks[1:], strict=False))  # consecutive chunks overlap
    assert all(c[1] - c[0] <= 1000 for c in chunks)
    assert len(svc.chunk_text(text, 1000, 100, 3)) == 3
    assert svc.chunk_text("short", 1000, 100, 3) == [(0, 5)]


def test_extraction_schema_is_strict_compatible():
    def check(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False and set(node["required"]) == set(node["properties"])
            for v in node.values():
                check(v)
        elif isinstance(node, list):
            for v in node:
                check(v)

    check(EXTRACTION_JSON_SCHEMA)
    assert "verified" not in str(EXTRACTION_JSON_SCHEMA)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["requirements"][0].update(category="poetry"),
        lambda d: d["requirements"][0]["parameters"][0].update(name="Bad Name"),
        lambda d: d["requirements"][0]["parameters"][0].update(confidence=3),
        lambda d: d["requirements"][0].pop("source"),
        lambda d: d.update(extra=1),
    ],
)
def test_malformed_model_output_is_rejected(mutate):
    data = fx.good_extraction()
    mutate(data)
    with pytest.raises(OutputInvalid):
        validate_raw(data)
