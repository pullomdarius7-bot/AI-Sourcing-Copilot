"""Parse a supplier's quote spreadsheet (CSV or XLSX) into quote lines. Deterministic: no AI, no guessing.

Columns are recognised by header name. Anything that cannot be read exactly is left empty and reported as a
warning; nothing is inferred. Line references (an "RFQ line" column) only become match suggestions that a person
still confirms.
"""
import csv
import io
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

MAX_ROWS = 500
HEADERS: dict[str, tuple[str, ...]] = {
    "line": ("rfq line", "line", "line no", "line number", "pos", "position", "item no", "rfq item"),
    "description": ("description", "item", "product", "article", "part", "material", "item description", "designation"),
    "quantity": ("quantity", "qty", "menge", "quantity offered"),
    "unit": ("unit", "uom", "unit of measure", "units", "einheit"),
    "unit_price": ("unit price", "price", "price per unit", "unit cost", "net price", "preis", "einzelpreis"),
    "total_price": ("total", "line total", "amount", "extended price", "total price", "gesamtpreis", "value"),
    "lead_time_days": ("lead time", "lead time days", "lead time (days)", "delivery days", "lead", "lieferzeit", "lead days"),
    "moq": ("moq", "minimum order quantity", "min qty", "min order qty", "minimum quantity", "mindestmenge"),
    "currency": ("currency", "ccy", "währung", "curr"),
}
_LOOKUP = {name: key for key, names in HEADERS.items() for name in names}
CURRENCY_RE = re.compile(r"\b([A-Z]{3})\b")


class IngestError(Exception):
    pass


@dataclass
class Ingested:
    lines: list[dict[str, Any]] = field(default_factory=list)
    currency: str | None = None
    warnings: list[str] = field(default_factory=list)
    rows_read: int = 0
    rows_skipped: int = 0
    columns: dict[str, str] = field(default_factory=dict)


def _header_key(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"[\s_]+", " ", str(value).strip().lower()).strip(" :")
    text = re.sub(r"\s*\((eur|usd|gbp|chf|[a-z]{3})\)$", "", text)
    return _LOOKUP.get(text)


def _clean(value: Any) -> str | None:
    text = re.sub(r"(?i)\b[a-z]{3}\b|[€$£¥\s\u00a0']", "", str(value).strip())
    return text if re.fullmatch(r"-?[\d.,]+", text) and re.search(r"\d", text) else None


def decimal_evidence(value: Any) -> str | None:
    """Which character a text cell proves to be the decimal separator, if any."""
    if not isinstance(value, str):
        return None
    text = _clean(value)
    if text is None:
        return None
    if "," in text and "." in text:
        return "," if text.rfind(",") > text.rfind(".") else "."
    for sep, other in ((",", "."), (".", ",")):
        if text.count(sep) > 1:
            return other  # repeated separator = thousands grouping
        if text.count(sep) == 1 and len(text.split(sep)[1]) != 3:
            return sep
    return None


def parse_number(value: Any, decimal: str | None = None) -> Decimal | None:
    """Exact number from a cell, or None when it cannot be read without guessing.

    decimal is the file's decimal separator (see decimal_evidence). When both ',' and '.' appear the last one is the
    decimal separator; a repeated separator is thousands grouping; a single separator followed by exactly three digits
    ("1,000", "5.000") is read with the file's convention and is left empty when the file gives none."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    text = _clean(value)
    if text is None:
        return None
    if "," in text and "." in text:
        decimal_sep = "," if text.rfind(",") > text.rfind(".") else "."
        text = text.replace("." if decimal_sep == "," else ",", "").replace(decimal_sep, ".")
    else:
        sep = "," if "," in text else "." if "." in text else None
        if sep and text.count(sep) > 1:
            text = text.replace(sep, "")
        elif sep:
            head, tail = text.split(sep)
            if len(tail) == 3 and head.lstrip("-"):
                if decimal is None:
                    return None
                text = head + "." + tail if decimal == sep else head + tail
            else:
                text = head + "." + tail
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _rows_from_csv(data: bytes) -> list[list[Any]]:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - cp1252 decodes almost anything
        raise IngestError("The file is not readable text")
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    delimiter = max(",;\t|", key=lambda d: (first.count(d), d == ","))  # most frequent in the header line
    return [row for row in csv.reader(io.StringIO(text), delimiter=delimiter)][: MAX_ROWS + 20]


def _rows_from_xlsx(data: bytes) -> list[list[Any]]:
    from openpyxl import load_workbook

    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # openpyxl raises many types for corrupt files
        raise IngestError(f"The workbook could not be read: {type(exc).__name__}") from exc
    try:
        ws = wb.worksheets[0]
        rows = []
        for row in ws.iter_rows(values_only=True, max_row=MAX_ROWS + 20):
            rows.append(list(row))
        return rows
    finally:
        wb.close()


def parse(filename: str, data: bytes) -> Ingested:
    name = filename.lower()
    if name.endswith(".csv") or name.endswith(".txt"):
        rows = _rows_from_csv(data)
    elif name.endswith(".xlsx"):
        rows = _rows_from_xlsx(data)
    else:
        raise IngestError("Upload a .csv or .xlsx file")

    header_index, columns = None, {}
    for i, row in enumerate(rows[:10]):
        mapping = {}
        for col, cell in enumerate(row):
            key = _header_key(cell)
            if key and key not in mapping:
                mapping[key] = col
        if "description" in mapping and len(mapping) >= 2:
            header_index, columns = i, mapping
            break
    if header_index is None:
        raise IngestError("No header row found. Name the columns, e.g. Description, Quantity, Unit, Unit price, Lead time, MOQ")

    numeric = [columns[k] for k in ("quantity", "unit_price", "total_price", "lead_time_days", "moq") if k in columns]
    evidence = {decimal_evidence(row[c]) for row in rows[header_index + 1:] for c in numeric if c < len(row)} - {None}
    decimal = evidence.pop() if len(evidence) == 1 else None
    out = Ingested(columns={k: str(rows[header_index][v]) for k, v in columns.items()})
    currencies: set[str] = set()
    for title in out.columns.values():
        match = re.search(r"\(([A-Za-z]{3})\)\s*$", title.strip())
        if match:
            currencies.add(match.group(1).upper())
    for offset, row in enumerate(rows[header_index + 1:], start=header_index + 2):
        def cell(key: str, row: list[Any] = row) -> Any:
            return row[columns[key]] if key in columns and columns[key] < len(row) else None

        description = cell("description")
        if all(v is None or str(v).strip() == "" for v in row):
            continue
        out.rows_read += 1
        if out.rows_read > MAX_ROWS:
            raise IngestError(f"The file has more than {MAX_ROWS} quote lines; split it into several quotes")
        if description is None or not str(description).strip():
            out.rows_skipped += 1
            out.warnings.append(f"Row {offset}: no description, skipped")
            continue
        line: dict[str, Any] = {"description": str(description).strip()[:2000], "row": offset}
        for key in ("quantity", "unit_price", "total_price", "lead_time_days", "moq"):
            raw = cell(key)
            value = parse_number(raw, decimal)
            if raw not in (None, "") and value is None:
                reason = ("is ambiguous (thousands or decimal separator?)" if _clean(raw) is not None
                          else "is not a number")
                out.warnings.append(f"Row {offset}: {key.replace('_', ' ')} '{raw}' {reason}, left empty")
            if value is not None and value < 0:
                out.warnings.append(f"Row {offset}: negative {key.replace('_', ' ')} ignored")
                value = None
            line[key] = value
        unit = cell("unit")
        line["unit"] = str(unit).strip()[:30] if unit not in (None, "") else None
        ref = parse_number(cell("line"), decimal)
        line["line_ref"] = int(ref) if ref is not None and ref == int(ref) and ref > 0 else None
        for source in (cell("currency"), cell("unit_price"), cell("total_price")):
            if isinstance(source, str):
                currencies.update(CURRENCY_RE.findall(source.upper()))
        out.lines.append(line)
    if len(currencies) == 1:
        out.currency = currencies.pop()
    elif len(currencies) > 1:
        out.warnings.append("Several currencies appear in the file (" + ", ".join(sorted(currencies)) + "); set the quote currency by hand")
    if len(evidence) > 1:
        out.warnings.append("The file mixes ',' and '.' as decimal separators; numbers like 1,000 or 1.000 were left empty")
    if not out.lines:
        raise IngestError("No quote lines found below the header row")
    return out
