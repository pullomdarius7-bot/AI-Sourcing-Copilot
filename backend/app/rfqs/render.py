"""PDF rendering for RFQs and comparisons (reportlab, self-contained)."""
import io
import os
from datetime import date
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.rfqs.content import PRIORITY_LABEL, format_number, parameter_text

FONT_DIRS = ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu", "/usr/local/share/fonts")
INK = colors.HexColor("#0f1b2d")
MUTED = colors.HexColor("#56637a")
RULE = colors.HexColor("#d5dbe5")
ACCENT = colors.HexColor("#0d6b62")
FALLBACK_MAP = {"≥": ">=", "≤": "<=", "≈": "~", "–": "-", "—": "-", "−": "-",
                "‘": "'", "’": "'", "“": '"', "”": '"', "…": "..."}


def _register_fonts() -> tuple[str, str, bool]:
    for folder in FONT_DIRS:
        regular, bold = os.path.join(folder, "DejaVuSans.ttf"), os.path.join(folder, "DejaVuSans-Bold.ttf")
        if os.path.exists(regular) and os.path.exists(bold):
            if "DejaVuSans" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("DejaVuSans", regular))
                pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", bold))
            return "DejaVuSans", "DejaVuSans-Bold", True
    return "Helvetica", "Helvetica-Bold", False


FONT, FONT_BOLD, UNICODE = _register_fonts()


def t(value: Any) -> str:
    """Escape user text for reportlab markup; degrade gracefully when no Unicode font is installed."""
    text = "" if value is None else str(value)
    if not UNICODE:
        text = "".join(FALLBACK_MAP.get(ch, ch) for ch in text).encode("latin-1", "replace").decode("latin-1")
    return escape(text).replace("\n", "<br/>")


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()["Normal"]
    def mk(name: str, **kw: Any) -> ParagraphStyle:
        kw.setdefault("fontName", FONT)
        kw.setdefault("textColor", INK)
        return ParagraphStyle(name, parent=base, **kw)

    return {
        "body": mk("body", fontSize=9.5, leading=13),
        "small": mk("small", fontSize=8, leading=10.5, textColor=MUTED),
        "cell": mk("cell", fontSize=8.5, leading=11),
        "cell_b": mk("cell_b", fontSize=8.5, leading=11, fontName=FONT_BOLD),
        "cell_r": mk("cell_r", fontSize=8.5, leading=11, alignment=TA_RIGHT),
        "h1": mk("h1", fontSize=18, leading=22, fontName=FONT_BOLD),
        "h2": mk("h2", fontSize=11.5, leading=15, fontName=FONT_BOLD, spaceBefore=10, spaceAfter=4),
        "eyebrow": mk("eyebrow", fontSize=8.5, leading=11, textColor=ACCENT, fontName=FONT_BOLD),
    }


def _grid(data: list[list[Any]], widths: list[float], header: bool = True, zebra: bool = True) -> Table:
    table = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f6")), ("LINEBELOW", (0, 0), (-1, 0), 0.8, INK)]
    if zebra:
        for r in range(2 if header else 1, len(data), 2):
            style.append(("BACKGROUND", (0, r), (-1, r), colors.HexColor("#f8fafc")))
    table.setStyle(TableStyle(style))
    return table


def _footer(left: str, draft: bool):
    def draw(canvas, doc) -> None:
        canvas.saveState()
        width, _ = doc.pagesize
        canvas.setFont(FONT, 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(doc.leftMargin, 10 * mm, left)
        canvas.drawRightString(width - doc.rightMargin, 10 * mm, f"Page {doc.page}")
        if draft:
            canvas.setFont(FONT_BOLD, 54)
            canvas.setFillColor(colors.Color(0.8, 0.1, 0.1, alpha=0.12))
            canvas.translate(width / 2, doc.pagesize[1] / 2)
            canvas.rotate(40)
            canvas.drawCentredString(0, 0, "DRAFT – NOT APPROVED" if UNICODE else "DRAFT - NOT APPROVED")
        canvas.restoreState()

    return draw


def render_rfq_pdf(document: dict[str, Any], *, status: str, content_hash: str | None, issued: date | None = None) -> bytes:
    s = _styles()
    draft = status in ("draft", "pending_approval")
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=18 * mm,
        title=f"{document['number']} {document['title']}", author=document["organization"],
        subject="Request for quotation",
    )
    story: list[Any] = [
        Paragraph(t(document["organization"]).upper(), s["eyebrow"]),
        Paragraph("Request for Quotation", s["h1"]),
        Spacer(1, 4),
        Paragraph(t(document["title"]), s["body"]),
        Spacer(1, 8),
    ]
    meta = [
        ["Reference", f"{document['number']} (version {document['version']})"],
        ["Issued", (issued or date.today()).isoformat()],
        ["Quotation due", document.get("response_due_date") or "Not set"],
        ["Currency", document["currency"]],
    ]
    terms = document.get("requested_terms") or {}
    if terms.get("required_by"):
        meta.append(["Delivery required by", terms["required_by"]])
    if terms.get("incoterms"):
        meta.append(["Incoterms requested", terms["incoterms"]])
    if terms.get("payment_terms"):
        meta.append(["Payment terms requested", terms["payment_terms"]])
    if document.get("category"):
        meta.append(["Category", document["category"]])
    if draft:
        meta.append(["Status", "Draft – not approved for release" if UNICODE else "Draft - not approved for release"])
    story.append(_grid([[Paragraph(t(k), s["cell_b"]), Paragraph(t(v), s["cell"])] for k, v in meta],
                       [38 * mm, 136 * mm], header=False, zebra=False))
    if document.get("message"):
        story += [Paragraph("Message", s["h2"]), Paragraph(t(document["message"]), s["body"])]

    story.append(Paragraph("Requirements", s["h2"]))
    rows: list[list[Any]] = [[Paragraph(h, s["cell_b"]) for h in ("#", "Requirement", "Priority", "Quantity")]]
    open_questions: list[str] = []
    for item in document["items"]:
        params = item.get("parameters") or []
        known = [p for p in params if not p.get("unknown")]
        unknown = [p for p in params if p.get("unknown")]
        body = t(item["statement"])
        if known:
            body += "<br/>" + "<br/>".join(f"<font color='#56637a'>{t(parameter_text(p))}</font>" for p in known)
        if unknown:
            body += "<br/>" + "<br/>".join(f"<font color='#9a5b00'>{t(parameter_text(p))}</font>" for p in unknown)
            open_questions += [f"Line {item['line']}: {p['name'].replace('_', ' ')}" for p in unknown]
        qty = ""
        if item.get("quantity") is not None:
            qty = format_number(float(item["quantity"])) + (f" {item['unit']}" if item.get("unit") else "")
        rows.append([
            Paragraph(str(item["line"]), s["cell"]), Paragraph(body, s["cell"]),
            Paragraph(t(PRIORITY_LABEL.get(item.get("priority") or "unknown", "Not stated")), s["cell"]),
            Paragraph(t(qty or "Not specified"), s["cell_r"]),
        ])
    story.append(_grid(rows, [10 * mm, 112 * mm, 24 * mm, 28 * mm]))

    if open_questions:
        story += [
            Paragraph("Information we need from you", s["h2"]),
            Paragraph("The following were not specified in our requirements. Please state your offer for each.", s["body"]),
            Spacer(1, 3),
            Paragraph("<br/>".join(f"• {t(q)}" if UNICODE else f"- {t(q)}" for q in open_questions), s["body"]),
        ]

    instructions = [
        f"Quote in {document['currency']} and reference {document['number']} in your reply.",
        "Give a unit price, quantity, unit, lead time in days and any minimum order quantity for each requirement line, using the line numbers above.",
        "State your Incoterms, payment terms and how long the quotation is valid.",
        "If you cannot meet a requirement, say so on that line rather than leaving it out.",
    ]
    if document.get("response_due_date"):
        instructions.insert(0, f"Send your quotation by {document['response_due_date']}.")
    story.append(KeepTogether([
        Paragraph("How to respond", s["h2"]),
        Paragraph("<br/>".join(f"{n}. {t(line)}" for n, line in enumerate(instructions, start=1)), s["body"]),
    ]))
    footer = f"{document['number']} v{document['version']}"
    if content_hash:
        footer += f" · content {content_hash[:16]}" if UNICODE else f" - content {content_hash[:16]}"
    doc.build(story, onFirstPage=_footer(footer, draft), onLaterPages=_footer(footer, draft))
    return buf.getvalue()


def _score(value: float | None) -> str:
    return "unknown" if value is None else f"{value * 100:.0f}"


def render_comparison_pdf(result: dict[str, Any], *, rfq_number: str, rfq_title: str, organization: str,
                          generated_at: str, award: dict[str, Any] | None = None) -> bytes:
    s = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4), leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=16 * mm,
        title=f"Comparison {rfq_number}", author=organization, subject="Supplier quote comparison",
    )
    currency = result["currency"]
    story: list[Any] = [
        Paragraph(t(organization).upper(), s["eyebrow"]),
        Paragraph(f"Supplier comparison – {t(rfq_number)}" if UNICODE else f"Supplier comparison - {t(rfq_number)}", s["h1"]),
        Paragraph(t(rfq_title), s["body"]),
        Paragraph(t(f"Generated {generated_at}. Weights: " + ", ".join(
            f"{k.replace('_', ' ')} {v * 100:.0f}%" for k, v in result["weights"].items())), s["small"]),
        Spacer(1, 6),
        Paragraph(t(result["decision_note"]), s["body"]),
    ]
    if award:
        story.append(Paragraph(t(f"Awarded to {award['supplier']} by {award['by']} on {award['at']}: {award['note']}"), s["cell_b"]))

    story.append(Paragraph("Ranking", s["h2"]))
    head = ["Rank", "Supplier", "Score", "Price", "Lead time", "Coverage", "Qualified", "Risk", f"Basket ({currency})", "Flags"]
    rows = [[Paragraph(h, s["cell_b"]) for h in head]]
    for r in result["ranking"]:
        c = r["criteria"]
        rows.append([
            Paragraph(str(r["rank"]), s["cell"]), Paragraph(t(f"{r['supplier_name']} ({r['supplier_code']})"), s["cell"]),
            Paragraph(f"{r['score']:.1f}", s["cell_r"]), Paragraph(_score(c["price"]["score"]), s["cell_r"]),
            Paragraph(_score(c["lead_time"]["score"]), s["cell_r"]), Paragraph(_score(c["coverage"]["score"]), s["cell_r"]),
            Paragraph("yes" if r["qualified"] else "no", s["cell"]),
            Paragraph(t(f"{r['risk']['score']} {r['risk']['level']}") if r.get("risk") else "–", s["cell"]),
            Paragraph(t(format_number(r["basket_total"])) if r["basket_total"] is not None else "unknown", s["cell_r"]),
            Paragraph(t(", ".join(r["flags"]) or "–"), s["small"]),
        ])
    story.append(_grid(rows, [12 * mm, 52 * mm, 15 * mm, 15 * mm, 18 * mm, 20 * mm, 19 * mm, 22 * mm, 28 * mm, 68 * mm]))

    if result["ranking"]:
        story.append(Paragraph(f"Line prices ({t(currency)}, unit price × requested quantity)" if UNICODE
                               else f"Line prices ({t(currency)})", s["h2"]))
        suppliers = [(r["supplier_id"], r["supplier_name"]) for r in result["ranking"]]
        width = (269 * mm - 78 * mm) / max(len(suppliers), 1)
        rows = [[Paragraph("Line", s["cell_b"]), Paragraph("Requirement", s["cell_b"])] +
                [Paragraph(t(name), ParagraphStyle("hr", parent=s["cell_b"], alignment=TA_RIGHT)) for _, name in suppliers]]
        for item in result["items"]:
            line = [Paragraph(str(item["line"]), s["cell"]), Paragraph(t(item["statement"][:160]), s["cell"])]
            for sid, _ in suppliers:
                cell = item["cells"].get(sid)
                if not cell or cell["comparable_total"] is None:
                    reason = ", ".join(cell["flags"]) if cell and cell["flags"] else "not quoted"
                    line.append(Paragraph(t(reason), ParagraphStyle("sr", parent=s["small"], alignment=TA_RIGHT)))
                else:
                    text = format_number(cell["comparable_total"])
                    if cell["flags"]:
                        text += f"<br/><font size='7' color='#9a5b00'>{t(', '.join(cell['flags']))}</font>"
                    line.append(Paragraph(text, s["cell_r"]))
            rows.append(line)
        story.append(_grid(rows, [12 * mm, 66 * mm] + [width] * len(suppliers)))

    if result["excluded"]:
        story.append(Paragraph("Not scored", s["h2"]))
        rows = [[Paragraph(h, s["cell_b"]) for h in ("Supplier", "Reason")]]
        rows += [[Paragraph(t(e["supplier_name"]), s["cell"]), Paragraph(t(e["detail"]), s["cell"])] for e in result["excluded"]]
        story.append(_grid(rows, [70 * mm, 199 * mm]))

    if result.get("lead_time_analysis") and result["ranking"]:
        lt = result["lead_time_analysis"]
        story.append(Paragraph("Lead time and minimum order quantities", s["h2"]))
        rows = [[Paragraph(h, s["cell_b"]) for h in ("Supplier", "Lead time (days)", "Slack (days)", "On time",
                                                     f"MOQ extra cost ({currency})")]]
        for r in result["ranking"]:
            on_time = "–" if r.get("on_time") is None else ("yes" if r["on_time"] else "no")
            rows.append([Paragraph(t(r["supplier_name"]), s["cell"]),
                         Paragraph(format_number(r["lead_time_days"]) if r["lead_time_days"] is not None else "not stated", s["cell_r"]),
                         Paragraph(format_number(r["slack_days"]) if r.get("slack_days") is not None else "–", s["cell_r"]),
                         Paragraph(on_time, s["cell"]),
                         Paragraph(t(format_number(r["moq_excess_total"])) if r.get("moq_excess_total") else "–", s["cell_r"])])
        story.append(_grid(rows, [90 * mm, 40 * mm, 35 * mm, 30 * mm, 74 * mm]))
        need = f"Required by {lt['required_by']} ({lt['available_days']} days from the analysis date)." if lt.get("required_by") \
            else "No required delivery date is set on the RFQ, so lead times are only compared with each other."
        story.append(Paragraph(t(need), s["small"]))

    if any(r.get("deviations") for r in result["ranking"]):
        story.append(Paragraph("Risk and deviations from the request", s["h2"]))
        rows = [[Paragraph(h, s["cell_b"]) for h in ("Supplier", "Severity", "Line", "Deviation")]]
        for r in result["ranking"]:
            for d in r.get("deviations", []):
                if d["severity"] == "info":
                    continue
                rows.append([Paragraph(t(r["supplier_name"]), s["cell"]), Paragraph(d["severity"], s["cell"]),
                             Paragraph(str(d["line"] or "–"), s["cell"]), Paragraph(t(d["message"]), s["small"])])
        if len(rows) > 1:
            story.append(_grid(rows, [60 * mm, 22 * mm, 14 * mm, 173 * mm]))
        story.append(Paragraph("Risk points: critical 25, major 10, minor 3 (capped at 100); the risk criterion scores 100 minus the points.", s["small"]))

    if result["global_flags"]:
        story += [Paragraph("Notes", s["h2"]), Paragraph("<br/>".join(t(f) for f in result["global_flags"]), s["body"])]

    story.append(Paragraph("How each score was reached", s["h2"]))
    for r in result["ranking"]:
        story.append(KeepTogether([
            Paragraph(t(f"{r['rank']}. {r['supplier_name']} – {r['score']:.1f}" if UNICODE else f"{r['rank']}. {r['supplier_name']} - {r['score']:.1f}"), s["cell_b"]),
            Paragraph("<br/>".join(t(x) for x in r["explanation"]), s["small"]),
            Spacer(1, 4),
        ]))
    footer = f"{rfq_number} comparison · inputs {result['inputs_sha256'][:16]}" if UNICODE else f"{rfq_number} comparison - inputs {result['inputs_sha256'][:16]}"
    doc.build(story, onFirstPage=_footer(footer, False), onLaterPages=_footer(footer, False))
    return buf.getvalue()
