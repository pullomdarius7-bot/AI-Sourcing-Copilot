"""Report export. A report is stored once with its statements and sources; exports render exactly that record."""
import io
from typing import Any

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer

from app.rfqs.render import _footer, _grid, _styles, t


def to_markdown(report: dict[str, Any]) -> str:
    lines = [f"# {report['title']}", "",
             f"Generated {report['created_at']} by the sourcing assistant. Every statement cites its sources; "
             f"figures come from the application's deterministic services. Content SHA-256 {report['content_sha256']}.", ""]
    for b in report["blocks"]:
        if b["type"] == "heading":
            lines += ["", f"## {b['text']}", ""]
        else:
            lines.append(f"- {b['text']} " + "".join(f"[{s}]" for s in b["sources"]))
    if report["unknowns"]:
        lines += ["", "## Unknown", ""] + [f"- {u}" for u in report["unknowns"]]
    lines += ["", "## Sources", ""]
    for s in report["sources"]:
        where = f" – {s['path']}" if s.get("path") else ""
        as_of = f" (as of {s['as_of']})" if s.get("as_of") else ""
        lines.append(f"- [{s['id']}] {s['title']}{as_of}{where}")
    return "\n".join(lines).strip() + "\n"


def to_pdf(report: dict[str, Any], organization: str) -> bytes:
    s = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=16 * mm, bottomMargin=18 * mm,
                            title=report["title"], author=organization, subject="Sourcing assistant report")
    story: list[Any] = [
        Paragraph(t(organization).upper(), s["eyebrow"]),
        Paragraph(t(report["title"]), s["h1"]),
        Paragraph(t(f"Generated {report['created_at']} by the sourcing assistant. Every statement cites its sources "
                    "(listed at the end); figures come from the application's deterministic services."), s["small"]),
        Spacer(1, 6),
    ]
    for b in report["blocks"]:
        if b["type"] == "heading":
            story.append(Paragraph(t(b["text"]), s["h2"]))
        else:
            cites = " ".join(f"[{x}]" for x in b["sources"])
            story.append(Paragraph(f"• {t(b['text'])} <font color='#0d6b62'>{t(cites)}</font>", s["body"]))
    if report["unknowns"]:
        story.append(Paragraph("Unknown", s["h2"]))
        story += [Paragraph(f"• {t(u)}", s["body"]) for u in report["unknowns"]]
    rows = [[Paragraph(h, s["cell_b"]) for h in ("Id", "Source", "As of", "Where")]]
    for src in report["sources"]:
        rows.append([Paragraph(t(src["id"]), s["cell"]), Paragraph(t(src["title"]), s["cell"]),
                     Paragraph(t(src.get("as_of") or "–"), s["cell"]), Paragraph(t(src.get("path") or "built-in"), s["small"])])
    story.append(KeepTogether([Paragraph("Sources", s["h2"]), _grid(rows, [12 * mm, 92 * mm, 24 * mm, 50 * mm])]))
    footer = f"{report['title'][:70]} · {report['content_sha256'][:16]}"
    doc.build(story, onFirstPage=_footer(footer, False), onLaterPages=_footer(footer, False))
    return buf.getvalue()
