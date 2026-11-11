"""Exportable deal sheets: a deep analysis as CSV (for spreadsheets) or PDF (to share or file)."""
import csv
import io
from datetime import datetime
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.config import get_settings
from app.marketplace.catalog import CONDITIONS

settings = get_settings()
COLUMNS = [("title", "Item"), ("condition", "Condition"), ("quantity", "Qty"), ("unit_msrp", "Unit MSRP"),
           ("resale_unit", "Resale / unit"), ("sellable_units", "Sellable"), ("revenue", "Revenue"), ("fees", "Fees"),
           ("refurbishment", "Refurb"), ("handling", "Handling"), ("freight", "Freight"), ("cost", "Cost"),
           ("net_profit", "Net profit")]


def _money(value: Any) -> str:
    return f"${float(value or 0):,.2f}"


def to_csv(result: dict[str, Any], lot_title: str) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Deal sheet", lot_title])
    writer.writerow(["Decision", result["decision"].upper(), "Max bid", result["max_bid"]])
    a = result["assumptions"]
    writer.writerow(["Channel", a["channel_label"], "Fees %", a["fee_rate_pct"], "Target net margin %",
                     a["target_margin_pct"], "Freight", a["freight"], "Asking price", a["asking_price"]])
    writer.writerow([])
    writer.writerow([label for _, label in COLUMNS] + ["Resale source"])
    for item in result["items"]:
        writer.writerow([CONDITIONS.get(item[k], item[k]) if k == "condition" else item.get(k, 0) for k, _ in COLUMNS]
                        + [item["resale_source"]])
    t = result["totals"]
    writer.writerow([])
    writer.writerow(["Totals", "", t["units"], t["msrp"], "", "", t["revenue"], t["fees"], t["refurbishment"],
                     t.get("handling", 0), t["freight"], t["cost"], t["net_profit"]])
    writer.writerow(["ROI %", t["roi_pct"], "Net margin %", t["net_margin_pct"]])
    return ("﻿" + out.getvalue()).encode("utf-8")  # BOM: Excel opens UTF-8 correctly


def to_pdf(result: dict[str, Any], lot_title: str, created_at: datetime) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(letter), leftMargin=0.5 * inch, rightMargin=0.5 * inch,
                            topMargin=0.5 * inch, bottomMargin=0.5 * inch, title=f"Deal sheet - {lot_title}")
    styles = getSampleStyleSheet()
    a, t = result["assumptions"], result["totals"]
    story = [
        Paragraph(f"Deal sheet: {lot_title}", styles["Title"]),
        Paragraph(f"{settings.platform_name} · analysed {created_at:%Y-%m-%d %H:%M} UTC", styles["Normal"]),
        Spacer(1, 10),
        Paragraph(f"<b>Decision: {result['decision'].upper()}</b> · max bid ceiling {_money(result['max_bid'])} "
                  f"at {a['target_margin_pct']}% target net margin · asking {_money(a['asking_price'])}", styles["Heading3"]),
        Paragraph(f"Expected revenue {_money(t['revenue'])} · fees {_money(t['fees'])} · refurbishment "
                  f"{_money(t['refurbishment'])} · handling {_money(t.get('handling', 0))} · freight {_money(t['freight'])}"
                  f" · net profit at asking {_money(t['net_profit'])} · ROI {t['roi_pct']}%", styles["Normal"]),
        Paragraph(f"Channel {a['channel_label']} ({a['fee_rate_pct']}% fees).", styles["Normal"]),
        Spacer(1, 10),
    ]
    cell = styles["BodyText"]
    cell.fontSize = 8
    cell.leading = 10
    rows = [[label for _, label in COLUMNS]]
    for item in result["items"]:
        row = []
        for key, _ in COLUMNS:
            value = item.get(key, 0)
            if key == "title":
                row.append(Paragraph(str(value)[:120], cell))
            elif key == "condition":
                row.append(CONDITIONS.get(value, value))
            elif key in ("quantity", "sellable_units"):
                row.append(str(value))
            else:
                row.append(_money(value))
        rows.append(row)
    rows.append(["Totals", "", str(t["units"]), _money(t["msrp"]), "", "", _money(t["revenue"]), _money(t["fees"]),
                 _money(t["refurbishment"]), _money(t.get("handling", 0)), _money(t["freight"]), _money(t["cost"]),
                 _money(t["net_profit"])])
    table = Table(rows, repeatRows=1, colWidths=[2.6 * inch] + [None] * (len(COLUMNS) - 1))
    table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e6efee")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c9d3d8")),
    ]))
    story += [table, Spacer(1, 8)]
    for note in result.get("notes", []) + ["Estimates only: inspect the lot and confirm freight before committing capital."]:
        story.append(Paragraph(note, styles["Italic"]))
    doc.build(story)
    return buffer.getvalue()
