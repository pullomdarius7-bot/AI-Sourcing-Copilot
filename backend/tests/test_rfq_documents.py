"""RFQ content hashing, PDF rendering, email packages, matching and Excel export (pure: no database needed)."""
import io
import json
from datetime import UTC, datetime

from openpyxl import load_workbook
from pypdf import PdfReader

from app.rfqs import comparison as cmp
from app.rfqs import content, matching, package, render, xlsx
from tests.rfq_fixtures import DOCUMENT, base_inputs


def pdf_text(data: bytes) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)


# --- content ------------------------------------------------------------------------------------------------
def test_build_document_orders_lines_and_keeps_unknowns():
    doc = content.build_document(
        number="RFQ-1", title="T", version=1, currency="EUR", response_due_date=None, message=None,
        organization="Org", category=None,
        items=[{"position": 2, "statement": "b", "parameters": []},
               {"position": 1, "statement": "a", "priority": "mandatory", "quantity": 3, "unit": "pcs",
                "parameters": [{"name": "x", "value": None, "unknown": True, "extra": "dropped"}]}],
    )
    assert [i["statement"] for i in doc["items"]] == ["a", "b"] and [i["line"] for i in doc["items"]] == [1, 2]
    assert doc["items"][0]["parameters"] == [{"name": "x", "value": None, "unit": None, "qualifier": None, "unknown": True}]
    assert doc["items"][1]["priority"] == "unknown" and doc["message"] == ""


def test_approval_hash_covers_content_and_recipients():
    h = content.approval_hash(DOCUMENT, ["b", "a"])
    assert h == content.approval_hash(DOCUMENT, ["a", "b"])  # order-independent
    assert h != content.approval_hash(DOCUMENT, ["a"])
    changed = json.loads(json.dumps(DOCUMENT))
    changed["items"][0]["quantity"] = 5001
    assert h != content.approval_hash(changed, ["a", "b"])


def test_requested_terms_are_optional_and_hashed():
    base = dict(number="R", title="T", version=1, currency="EUR", response_due_date=None, message=None, organization="O",
                category=None, items=[])
    plain = content.build_document(**base)
    assert "requested_terms" not in plain  # documents approved before Sprint 5 keep their approval hash
    with_terms = content.build_document(**base, required_by="2026-12-08", incoterms="DAP")
    assert with_terms["requested_terms"] == {"required_by": "2026-12-08", "incoterms": "DAP"}
    assert content.approval_hash(plain, []) != content.approval_hash(with_terms, [])
    text = pdf_text(render.render_rfq_pdf(with_terms, status="approved", content_hash="a" * 64))
    assert "Delivery required by" in text and "DAP" in text


def test_parameter_text_never_invents_values():
    assert content.parameter_text({"name": "grade_class", "unknown": True}) == "grade class: not specified – please state"
    assert content.parameter_text({"name": "thickness", "value": 3, "unit": "mm", "qualifier": "min"}) == "thickness: ≥ 3 mm"
    assert content.parameter_text({"name": "qty", "value": 5000.0}) == "qty: 5,000"
    assert content.parameter_text({"name": "tol", "value": 0.1, "unit": "mm"}) == "tol: 0.1 mm"


# --- PDF --------------------------------------------------------------------------------------------------------
def test_rfq_pdf_contains_the_request_and_escapes_markup():
    data = render.render_rfq_pdf(DOCUMENT, status="approved", content_hash="ab" * 32)
    assert data.startswith(b"%PDF-")
    reader = PdfReader(io.BytesIO(data))
    assert reader.metadata.title.startswith("RFQ-2026-0001")
    text = pdf_text(data)
    for expected in ("Request for Quotation", "RFQ-2026-0001 (version 2)", "2026-12-01", "<AISI 304> & co",
                     "Information we need from you", "Line 1: grade class", "Line 2: warranty period",
                     "How to respond", "content abababababababab"):
        assert expected in text, expected
    assert "DRAFT" not in text


def test_draft_pdf_is_watermarked():
    text = pdf_text(render.render_rfq_pdf(DOCUMENT, status="draft", content_hash=None))
    assert "DRAFT" in text and "not approved" in text


def test_long_rfq_paginates():
    doc = json.loads(json.dumps(DOCUMENT))
    doc["items"] = [dict(doc["items"][0], line=n) for n in range(1, 120)]
    data = render.render_rfq_pdf(doc, status="approved", content_hash="c" * 64)
    assert len(PdfReader(io.BytesIO(data)).pages) > 2 and "Page 3" in pdf_text(data)


def test_comparison_pdf():
    result = cmp.compare(base_inputs())
    data = render.render_comparison_pdf(result, rfq_number="RFQ-9", rfq_title="Brackets", organization="Org",
                                        generated_at="2026-11-10 09:00", award={"supplier": "Northwind Metals",
                                        "by": "ada@acme.test", "at": "2026-11-10", "note": "Best overall"})
    text = pdf_text(data)
    for expected in ("Supplier comparison", "Ranking", "Northwind Metals", "Kessler GmbH", "How each score was reached",
                     "decision support only", "Awarded to Northwind Metals", "Incoterms differ"):
        assert expected in text, expected


def test_comparison_pdf_shows_risk_deviations_lead_time_and_moq():
    inputs = base_inputs()
    inputs["required_by"] = "2026-12-08"
    inputs["quotes"][2]["lines"][0]["moq"] = 10000
    text = pdf_text(render.render_comparison_pdf(cmp.compare(inputs), rfq_number="RFQ-9", rfq_title="Brackets",
                                                 organization="Org", generated_at="2026-11-10 09:00", award=None))
    for expected in ("Risk and deviations", "Lead time and minimum order quantities", "Required by 2026-12-08",
                     "not currently qualified", "minimum order", "Risk points"):
        assert expected in text, expected


def test_exports_still_render_stored_version_1_results():
    """Comparisons stored before the analysis engine (no risk, deviations or lead-time keys) still export."""
    result = cmp.compare(base_inputs())
    for r in result["ranking"]:
        for key in ("risk", "deviations", "on_time", "slack_days", "moq_excess_total"):
            r.pop(key)
    for key in ("lead_time_analysis", "moq_analysis", "fx_used", "engine_version"):
        result.pop(key)
    assert "Ranking" in pdf_text(render.render_comparison_pdf(result, rfq_number="R", rfq_title="T", organization="O",
                                                              generated_at="now", award=None))
    wb = load_workbook(io.BytesIO(xlsx.comparison_workbook(result, rfq_number="R", rfq_title="T", generated_at="now")))
    assert wb["Deviations"].max_row == 1 and wb["About"]["B8"].value == "1"


def test_comparison_workbook_has_deviation_sheet():
    wb = load_workbook(io.BytesIO(xlsx.comparison_workbook(cmp.compare(base_inputs()), rfq_number="R", rfq_title="T",
                                                           generated_at="now")))
    assert wb.sheetnames == ["Ranking", "Line prices", "Not scored", "Deviations", "Explanation", "About"]
    codes = {row[3] for row in wb["Deviations"].iter_rows(min_row=2, values_only=True)}
    assert {"supplier_not_qualified", "line_not_quoted"} <= codes
    assert wb["Ranking"].cell(row=1, column=15).value == "Risk points"


# --- Email package ------------------------------------------------------------------------------------------
RECIPIENTS = [{"name": "Bob Sales", "email": "bob@northwind.test", "is_primary": False},
              {"name": "Ann Primary", "email": "ann@northwind.test", "is_primary": True}]


def build():
    pdf = render.render_rfq_pdf(DOCUMENT, status="approved", content_hash="d" * 64)
    return pdf, package.build_package(
        DOCUMENT, content_hash="d" * 64, pdf=pdf, sender="Sourcing <no-reply@acme.test>", reply_to="buyer@acme.test",
        reply_to_name="Ada Buyer", recipients=RECIPIENTS, message_domain="acme.test",
        generated_at=datetime(2026, 11, 10, 9, 0, tzinfo=UTC),
    )


def test_package_is_a_complete_message_with_attachments():
    pdf, (raw, manifest) = build()
    msg = package.parse_package(raw)
    assert msg["Subject"] == "RFQ-2026-0001 - Request for quotation: Stainless brackets – 3 mm"
    assert "ann@northwind.test" in msg["To"] and "bob@northwind.test" in msg["Cc"]
    assert msg["Reply-To"] == "Ada Buyer <buyer@acme.test>" and msg["X-RFQ-Content-SHA256"] == "d" * 64
    assert package.recipients_of(msg) == ["ann@northwind.test", "bob@northwind.test"]
    body = msg.get_body(preferencelist=("plain",)).get_content()
    assert body.startswith("Dear Ann Primary,") and "keep the reference RFQ-2026-0001" in body
    assert "grade class: not specified" in body and "by 2026-12-01" in body
    attachments = {a.get_filename(): a.get_content() for a in msg.iter_attachments()}
    assert attachments["RFQ-2026-0001-v2.pdf"] == pdf
    machine = json.loads(attachments["RFQ-2026-0001-v2.json"])
    assert machine["rfq"] == DOCUMENT and machine["content_sha256"] == "d" * 64
    assert [a["filename"] for a in manifest["attachments"]] == ["RFQ-2026-0001-v2.pdf", "RFQ-2026-0001-v2.json"]
    assert manifest["to"] == ["ann@northwind.test"] and manifest["cc"] == ["bob@northwind.test"]


def test_package_integrity_and_header_injection():
    _, (raw, _) = build()
    import hashlib

    digest = hashlib.sha256(raw).hexdigest()
    assert package.verify_package(raw, digest) and not package.verify_package(raw + b" ", digest)
    evil = dict(DOCUMENT, title="x\r\nBcc: victim@evil.test")
    raw2, _ = package.build_package(evil, content_hash="e" * 64, pdf=b"%PDF-1.4", sender="s@acme.test", reply_to=None,
                                    reply_to_name=None, recipients=RECIPIENTS[:1], message_domain="acme.test")
    msg = package.parse_package(raw2)
    assert msg["Bcc"] is None and "Bcc: victim" in msg["Subject"]


def test_package_requires_a_recipient():
    import pytest

    with pytest.raises(ValueError):
        package.build_package(DOCUMENT, content_hash="f" * 64, pdf=b"x", sender="s@a.test", reply_to=None,
                              reply_to_name=None, recipients=[], message_domain="a.test")


# --- Matching -----------------------------------------------------------------------------------------------
def test_matching_suggests_one_to_one_and_leaves_unknown_lines_unmatched():
    items = [
        {"id": "i1", "statement": "Stainless steel bracket AISI 304 3 mm", "parameters": [{"name": "material", "value": "AISI 304"}]},
        {"id": "i2", "statement": "Brushed surface finish", "parameters": []},
        {"id": "i3", "statement": "ISO 9001 certificate", "parameters": []},
    ]
    lines = [{"description": "Bracket, stainless AISI 304, 3mm thick"}, {"description": "Surface finish: brushed"},
             {"description": "Freight to Rotterdam"}, {"description": "Bracket stainless spare"}]
    result = matching.suggest(lines, items)
    assert result[0][0] == "i1" and result[1][0] == "i2"
    assert result[2] == (None, 0.0)
    assert result[3][0] is None  # i1 is already taken: never two lines on one requirement by suggestion
    assert matching.similarity("", "anything") == 0.0


# --- Excel ------------------------------------------------------------------------------------------------------
def test_comparison_workbook_and_formula_injection():
    inputs = base_inputs()
    inputs["suppliers"][0]["name"] = "=HYPERLINK(\"http://evil\")"
    result = cmp.compare(inputs)
    data = xlsx.comparison_workbook(result, rfq_number="RFQ-9", rfq_title="Brackets", generated_at="2026-11-10")
    wb = load_workbook(io.BytesIO(data))
    assert wb.sheetnames == ["Ranking", "Line prices", "Not scored", "Deviations", "Explanation", "About"]
    names = [c.value for c in wb["Ranking"]["B"][1:]]
    assert any(n.startswith("'=HYPERLINK") for n in names)
    assert all(c.data_type != "f" for row in wb["Ranking"].iter_rows() for c in row)
    assert wb["About"]["B7"].value == result["inputs_sha256"]
