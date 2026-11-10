"""Unit conversion, exchange rates and spreadsheet ingestion (pure: no database needed)."""
import io
from datetime import date
from decimal import Decimal

import pytest
from openpyxl import Workbook

from app.analysis import fx, ingest, units

D = Decimal


# --- Units ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("a,b,expected", [
    ("kg", "g", "1000"), ("g", "kg", "0.001"), ("lb", "kg", "0.45359237"), ("ft", "m", "0.3048"),
    ("dozen", "pcs", "12"), ("m3", "l", "1000"), ("ft2", "m2", "0.09290304"), ("Pieces", "pcs", "1"),
])
def test_standard_factors_are_exact(a, b, expected):
    conv = units.factor(a, b)
    assert conv is not None and conv.factor == D(expected) and conv.custom is False


def test_different_dimensions_need_a_rule():
    assert units.factor("kg", "pcs") is None and units.factor("m", "l") is None
    assert units.factor("widget", "pcs") is None


def test_custom_rules_direct_inverse_and_chained():
    rules = [{"from_unit": "box", "to_unit": "pcs", "factor": "50"}, {"from_unit": "pcs", "to_unit": "kg", "factor": "0.2"}]
    assert units.factor("box", "pcs", rules).factor == D(50)
    assert units.factor("pcs", "box", rules).factor == D(1) / D(50)
    assert units.factor("box", "dozen", rules).factor.quantize(D("1e-9")) == D("4.166666667")  # rule then standard step
    assert units.factor("pcs", "g", rules).factor == D("200")
    assert units.factor("box", "pcs", rules).custom is True


def test_round_trip_is_identity():
    for a, b in (("kg", "lb"), ("in", "mm"), ("gal", "l")):
        assert (units.factor(a, b).factor * units.factor(b, a).factor).quantize(D("1e-12")) == D(1)


# --- Exchange rates --------------------------------------------------------------------------------------
RATES = [
    {"base": "EUR", "quote": "USD", "rate": "1.10", "valid_from": "2026-10-01"},
    {"base": "EUR", "quote": "USD", "rate": "1.08", "valid_from": "2026-11-01"},
    {"base": "EUR", "quote": "USD", "rate": "2.00", "valid_from": "2026-12-01"},
    {"base": "GBP", "quote": "EUR", "rate": "1.15", "valid_from": "2026-11-05"},
]


def test_latest_rate_on_or_before_the_date():
    r = fx.find_rate("EUR", "USD", date(2026, 11, 10), RATES)
    assert r.rate == D("1.08") and r.age_days == 9 and r.valid_from == date(2026, 11, 1)
    assert fx.find_rate("EUR", "USD", date(2026, 10, 15), RATES).rate == D("1.10")


def test_inverse_identity_and_missing():
    assert fx.find_rate("USD", "EUR", date(2026, 11, 10), RATES).rate == D(1) / D("1.08")
    assert fx.find_rate("EUR", "EUR", date(2026, 11, 10), []).rate == 1
    assert fx.find_rate("USD", "GBP", date(2026, 11, 10), RATES) is None  # no triangulation
    assert fx.find_rate("EUR", "USD", date(2026, 9, 1), RATES) is None


def test_same_day_direct_rate_beats_inverted():
    rates = [{"base": "USD", "quote": "EUR", "rate": "0.5", "valid_from": "2026-11-01"},
             {"base": "EUR", "quote": "USD", "rate": "1.08", "valid_from": "2026-11-01"}]
    assert fx.find_rate("EUR", "USD", date(2026, 11, 10), rates).rate == D("1.08")


# --- Ingestion -------------------------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("1.234,56", "1234.56"), ("1,234.56", "1234.56"), ("4,20", "4.20"), ("1,000,000", "1000000"), ("EUR 12.50", "12.50"),
    ("€ 3", "3"), (" 7 ", "7"), (12, "12"), (2.5, "2.5"), ("1.2345", "1.2345"),
])
def test_parse_number(raw, expected):
    assert ingest.parse_number(raw) == D(expected)


@pytest.mark.parametrize("raw", ["n/a", "", None, "on request", True, "1,000", "5.000"])
def test_parse_number_leaves_unclear_values_empty(raw):
    assert ingest.parse_number(raw) is None


@pytest.mark.parametrize("raw,decimal,expected", [
    ("1,000", ".", "1000"), ("1,000", ",", "1.000"), ("5.000", ",", "5000"), ("5.000", ".", "5.000"),
])
def test_file_convention_resolves_three_digit_groups(raw, decimal, expected):
    assert ingest.parse_number(raw, decimal) == D(expected)


def test_decimal_evidence():
    assert [ingest.decimal_evidence(v) for v in ("4,20", "1.234,5", "1,234,567", "1,000", 3.5, "abc")] == \
        [",", ",", ".", None, None, None]


def test_ambiguous_numbers_without_evidence_are_left_empty_with_a_warning():
    res = ingest.parse("q.csv", b"Description,Qty\nA,1.500\n")
    assert res.lines[0]["quantity"] is None and any("ambiguous" in w for w in res.warnings)


def test_csv_with_semicolons_and_header_currency():
    data = (b"Pos;Description;Qty;Unit;Unit price (EUR);Lead time;MOQ\n"
            b"1;Bracket AISI 304;5.000;pcs;4,20;21;1000\n"
            b"2;Brushed finish;5000;pcs;n/a;21;\n"
            b";;;;;;\n"
            b"3;;1;pcs;1;;\n")
    res = ingest.parse("quote.csv", data)
    assert res.currency == "EUR" and len(res.lines) == 2 and res.rows_skipped == 1
    first = res.lines[0]
    assert first["quantity"] == D(5000) and first["unit_price"] == D("4.20") and first["moq"] == D(1000)
    assert first["lead_time_days"] == D(21) and first["line_ref"] == 1
    assert res.lines[1]["unit_price"] is None
    assert any("not a number" in w for w in res.warnings) and any("no description" in w for w in res.warnings)


def test_xlsx_with_title_rows_and_currency_column():
    wb = Workbook()
    ws = wb.active
    ws.append(["Quotation 4711"])
    ws.append([])
    ws.append(["Item", "Quantity", "UoM", "Price", "Currency", "Total"])
    ws.append(["Bracket", 5000, "pcs", 3.95, "USD", 19750])
    ws.append(["Finish", 5000, "pcs", 0.3, "USD", 1500])
    buf = io.BytesIO()
    wb.save(buf)
    res = ingest.parse("quote.xlsx", buf.getvalue())
    assert res.currency == "USD" and [ln["description"] for ln in res.lines] == ["Bracket", "Finish"]
    assert res.lines[0]["total_price"] == D(19750) and res.columns["unit_price"] == "Price"


def test_mixed_currencies_warn():
    res = ingest.parse("q.csv", b"Description,Price,Currency\nA,1,EUR\nB,2,USD\n")
    assert any("currenc" in w.lower() for w in res.warnings)


@pytest.mark.parametrize("name,data", [
    ("q.csv", b""), ("q.csv", b"foo,bar\n1,2\n"), ("q.pdf", b"%PDF-1.4"), ("q.xlsx", b"not a zip"),
])
def test_unreadable_files_raise(name, data):
    with pytest.raises(ingest.IngestError):
        ingest.parse(name, data)


def test_row_limit():
    rows = "Description,Price\n" + "".join(f"Item {i},1\n" for i in range(ingest.MAX_ROWS + 1))
    with pytest.raises(ingest.IngestError, match=f"more than {ingest.MAX_ROWS}"):
        ingest.parse("q.csv", rows.encode())


def test_ingestion_is_deterministic():
    data = b"Description,Qty,Price\nA,1,2\n"
    assert ingest.parse("q.csv", data) == ingest.parse("q.csv", data)
