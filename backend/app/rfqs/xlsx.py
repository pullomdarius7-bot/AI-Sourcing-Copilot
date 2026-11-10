"""Comparison export to Excel (openpyxl)."""
import io
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

HEADER_FILL = PatternFill("solid", fgColor="EEF2F6")
BOLD = Font(bold=True)


def cell_text(value: Any) -> Any:
    """Strings starting with a formula character are written as text, never evaluated."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def _sheet(ws, header: list[str], rows: list[list[Any]], widths: list[int]) -> None:
    ws.append(header)
    for cell in ws[1]:
        cell.font, cell.fill = BOLD, HEADER_FILL
    for row in rows:
        ws.append([cell_text(v) for v in row])
    for i, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = "A2"
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)


def comparison_workbook(result: dict[str, Any], *, rfq_number: str, rfq_title: str, generated_at: str) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Ranking"
    pct = lambda v: None if v is None else round(v * 100, 1)  # noqa: E731
    _sheet(ws, ["Rank", "Supplier", "Code", "Score", "Price", "Lead time", "Coverage", "Qualification",
                f"Basket total ({result['currency']})", "Lead time (days)", "Incoterms", "Payment terms", "Valid until", "Flags",
                "Risk points", "Risk level", "On time", f"MOQ extra cost ({result['currency']})"],
           [[r["rank"], r["supplier_name"], r["supplier_code"], r["score"], pct(r["criteria"]["price"]["score"]),
             pct(r["criteria"]["lead_time"]["score"]), pct(r["criteria"]["coverage"]["score"]),
             pct(r["criteria"]["qualification"]["score"]), r["basket_total"], r["lead_time_days"], r["incoterms"],
             r["payment_terms"], r["valid_until"], ", ".join(r["flags"]),
             (r.get("risk") or {}).get("score"), (r.get("risk") or {}).get("level"),
             None if r.get("on_time") is None else ("yes" if r["on_time"] else "no"), r.get("moq_excess_total")]
            for r in result["ranking"]],
           [6, 32, 12, 8, 8, 10, 10, 13, 18, 14, 11, 20, 12, 50, 11, 11, 9, 18])

    suppliers = [(r["supplier_id"], r["supplier_name"]) for r in result["ranking"]]
    prices = wb.create_sheet("Line prices")
    header = ["Line", "Requirement", "Quantity", "Unit"]
    for _, name in suppliers:
        header += [f"{name} unit price", f"{name} total", f"{name} flags"]
    rows = []
    for item in result["items"]:
        row: list[Any] = [item["line"], item["statement"], item["quantity"], item["unit"]]
        for sid, _ in suppliers:
            c = item["cells"].get(sid) or {}
            row += [c.get("unit_price"), c.get("comparable_total"), ", ".join(c.get("flags", []))]
        rows.append(row)
    _sheet(prices, header, rows, [6, 50, 10, 8] + [14, 14, 24] * len(suppliers))

    excluded = wb.create_sheet("Not scored")
    _sheet(excluded, ["Supplier", "Reason", "Detail"],
           [[e["supplier_name"], e["reason"], e["detail"]] for e in result["excluded"]], [32, 18, 80])

    deviations = wb.create_sheet("Deviations")
    _sheet(deviations, ["Supplier", "Severity", "Line", "Code", "Deviation"],
           [[r["supplier_name"], d["severity"], d["line"], d["code"], d["message"]]
            for r in result["ranking"] for d in r.get("deviations", [])], [32, 10, 6, 26, 100])

    explain = wb.create_sheet("Explanation")
    _sheet(explain, ["Rank", "Supplier", "Explanation"],
           [[r["rank"], r["supplier_name"], "\n".join(r["explanation"])] for r in result["ranking"]], [6, 32, 120])

    meta = wb.create_sheet("About")
    _sheet(meta, ["Field", "Value"], [
        ["RFQ", rfq_number], ["Title", rfq_title], ["Generated", generated_at], ["Currency", result["currency"]],
        ["As of", result["as_of"]], ["Inputs SHA-256", result["inputs_sha256"]],
        ["Engine version", result.get("engine_version", "1")],
        *[[f"Weight: {k}", v] for k, v in result["weights"].items()],
        *[[f"Exchange rate {c}", info["path"]] for c, info in (result.get("fx_used") or {}).items()],
        ["Notes", "\n".join(result["global_flags"]) or "-"], ["Decision", result["decision_note"]],
    ], [22, 100])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
