"""Quote analysis engine (deterministic).

Pipeline for every RFQ line x supplier:
  1. use only lines a person confirmed as answering that RFQ line
  2. unit: convert the quoted unit to the requested unit with an exact factor (built-in or organization rule)
  3. price: unit price per requested unit; derived from a line total only when the quoted quantity is stated
  4. currency: convert to the RFQ currency with the organization's recorded rate valid on the analysis date
  5. MOQ: quantity actually payable = max(requested quantity, MOQ); the difference is reported as MOQ excess cost
Then per supplier: lead time against the required date, deviations from the request, a risk score built only from
those deviations, and a weighted score. Nothing is estimated: what cannot be computed exactly is reported as such.
"""
import statistics
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from app.analysis import fx, units
from app.rfqs.content import sha256_of

ENGINE_VERSION = "2.0"
D = Decimal
DEFAULT_WEIGHTS = {"price": 0.45, "lead_time": 0.2, "coverage": 0.15, "qualification": 0.1, "risk": 0.1}
PRIORITY_WEIGHT = {"mandatory": D(2), "preferred": D(1), "optional": D("0.5"), "unknown": D(1)}
SEVERITY_POINTS = {"critical": 25, "major": 10, "minor": 3, "info": 0}
SEVERITY_ORDER = {"critical": 0, "major": 1, "minor": 2, "info": 3}
DECISION_NOTE = (
    "This analysis is decision support only. Every figure is computed deterministically from verified quote data, "
    "recorded exchange rates and unit rules; nothing is estimated. The award needs a recommendation approved by a person."
)


class AnalysisError(ValueError):
    pass


def normalise_weights(weights: dict[str, float] | None) -> dict[str, float]:
    raw = dict(DEFAULT_WEIGHTS if weights is None else weights)
    unknown = set(raw) - set(DEFAULT_WEIGHTS)
    if unknown:
        raise AnalysisError(f"Unknown criteria: {', '.join(sorted(unknown))}")
    if any(float(v) < 0 for v in raw.values()):
        raise AnalysisError("Weights cannot be negative")
    full = {k: float(raw.get(k, 0.0)) for k in DEFAULT_WEIGHTS}
    total = sum(full.values())
    if total <= 0:
        raise AnalysisError("At least one weight must be positive")
    return {k: round(v / total, 6) for k, v in full.items()}


def dec(value: Any) -> Decimal | None:
    return None if value is None else D(str(value))


def out(value: Decimal | None, places: int = 6) -> float | None:
    if value is None:
        return None
    return float(value.quantize(D(1).scaleb(-places), rounding=ROUND_HALF_EVEN))


def inputs_hash(inputs: dict[str, Any]) -> str:
    """Fingerprint of everything the analysis depends on except the weights."""
    keys = ("currency", "as_of", "required_by", "requested_incoterms", "requested_payment_terms", "items", "suppliers",
            "quotes", "fx_rates", "unit_conversions")
    return sha256_of({k: inputs.get(k) for k in keys})


def _norm_text(value: str | None) -> str:
    return " ".join((value or "").lower().replace(",", " ").split())


# --- Per-line normalization ------------------------------------------------------------------------------------
def normalize_cell(item: dict[str, Any], lines: list[dict[str, Any]], quote_ccy: str, ctx: dict[str, Any]) -> dict[str, Any]:
    cell: dict[str, Any] = {
        "unit_price": None, "comparable_total": None, "requested_total": None, "moq_excess": None, "lead_time_days": None,
        "moq": None, "payable_quantity": None, "flags": [], "conversions": [], "fx": None, "confidence": None,
    }
    if not lines:
        cell["flags"].append("not_quoted")
        return cell
    if len(lines) > 1:
        cell["flags"].append("multiple_lines_for_item")
        return cell
    line = lines[0]
    cell["confidence"] = line.get("confidence", 1.0)
    cell["lead_time_days"] = line.get("lead_time_days")
    if cell["confidence"] is not None and cell["confidence"] < 0.6:
        cell["flags"].append("low_confidence")

    req_unit, line_unit = units.canonical(item.get("unit")), units.canonical(line.get("unit"))
    factor = D(1)
    if req_unit and line_unit and req_unit != line_unit:
        conv = units.factor(line_unit, req_unit, ctx["unit_conversions"])
        if conv is None:
            cell["flags"].append("unit_not_convertible")
            return cell
        factor = conv.factor
        cell["flags"].append("unit_converted")
        cell["conversions"].append(conv.path)
    elif req_unit and not line_unit:
        cell["flags"].append("unit_not_stated")

    qty_line = dec(line.get("quantity"))
    unit_price, total = dec(line.get("unit_price")), dec(line.get("total_price"))
    if unit_price is not None and qty_line is not None and total is not None and total > 0:
        if abs(unit_price * qty_line - total) / total > D("0.01"):
            cell["flags"].append("arithmetic_mismatch")
    if unit_price is None and total is not None and qty_line:
        unit_price = total / qty_line
        cell["flags"].append("unit_price_from_total")
    if unit_price is None:
        cell["flags"].append("price_not_stated" if total is None else "price_not_comparable")
        return cell

    price_req_unit = unit_price / factor  # price per requested unit, quote currency
    rate = ctx["rates"].get(quote_ccy)
    if rate is None:
        cell["flags"].append("currency_not_convertible")
        return cell
    if rate.rate != 1:
        cell["fx"] = rate.path
    price = price_req_unit * rate.rate
    cell["unit_price"] = out(price)

    qty_req = dec(item.get("quantity"))
    moq = dec(line.get("moq"))
    moq_req = moq * factor if moq is not None else None
    if moq_req is not None:
        cell["moq"] = out(moq_req)
    if qty_req is None:
        cell["flags"].append("requested_quantity_unknown")
        cell["comparable_total"] = out(price)  # compared per unit, the same way for every supplier
        return cell
    if qty_line is not None and abs(qty_line * factor - qty_req) > D("0.000001"):
        cell["flags"].append("quantity_differs")
    payable = max(qty_req, moq_req) if moq_req is not None else qty_req
    requested_total = price * qty_req
    effective = price * payable
    cell["requested_total"] = out(requested_total)
    cell["comparable_total"] = out(effective)
    cell["payable_quantity"] = out(payable)
    if payable > qty_req:
        cell["flags"].append("moq_above_quantity")
        cell["moq_excess"] = out(effective - requested_total)
    return cell


# --- Deviations ------------------------------------------------------------------------------------------------------
def _dev(code: str, severity: str, message: str, line: int | None = None) -> dict[str, Any]:
    return {"code": code, "severity": severity, "line": line, "message": message}


def detect_deviations(quote: dict[str, Any], supplier: dict[str, Any], items: list[dict[str, Any]],
                      cells: dict[str, dict[str, Any]], ctx: dict[str, Any], lead: Decimal | None) -> list[dict[str, Any]]:
    devs: list[dict[str, Any]] = []
    as_of: date = ctx["as_of"]
    if not supplier.get("qualified"):
        devs.append(_dev("supplier_not_qualified", "critical", "The supplier is not currently qualified."))
    ccy = quote.get("currency")
    rate = ctx["rates"].get(ccy) if ccy else None
    if ccy and ccy != ctx["currency"] and rate is not None:
        devs.append(_dev("currency_converted", "info", f"Quoted in {ccy}; converted with {rate.path}."))
        if rate.age_days > ctx["fx_max_age_days"]:
            devs.append(_dev("fx_rate_stale", "minor", f"The {ccy} rate is {rate.age_days} days old."))

    requested_inco = (ctx.get("requested_incoterms") or "").upper()
    if not quote.get("incoterms"):
        devs.append(_dev("incoterms_not_stated", "minor", "No Incoterms stated."))
    elif requested_inco and quote["incoterms"].upper() != requested_inco:
        devs.append(_dev("incoterms_differ", "major", f"Quoted {quote['incoterms']}, requested {requested_inco}; prices are not on the same delivery basis."))
    if not quote.get("payment_terms"):
        devs.append(_dev("payment_terms_not_stated", "minor", "No payment terms stated."))
    elif ctx.get("requested_payment_terms") and _norm_text(quote["payment_terms"]) != _norm_text(ctx["requested_payment_terms"]):
        devs.append(_dev("payment_terms_differ", "minor", f"Payment terms '{quote['payment_terms']}' instead of '{ctx['requested_payment_terms']}'."))
    valid = quote.get("valid_until")
    if not valid:
        devs.append(_dev("validity_not_stated", "minor", "No quotation validity stated."))
    else:
        valid_d = date.fromisoformat(valid)
        if valid_d < as_of:
            devs.append(_dev("quote_expired", "critical", f"The quotation expired on {valid}."))
        elif valid_d <= as_of + timedelta(days=ctx["validity_warning_days"]):
            devs.append(_dev("validity_ends_soon", "minor", f"The quotation is only valid until {valid}."))

    for n, item in enumerate(items, start=1):
        cell = cells[item["id"]]
        prio = item.get("priority", "unknown")
        flags = cell["flags"]
        if "not_quoted" in flags:
            sev = {"mandatory": "critical", "preferred": "major"}.get(prio, "minor")
            devs.append(_dev("line_not_quoted", sev, f"Line {n} ({prio}) was not quoted.", n))
            continue
        for flag, sev, text in (
            ("multiple_lines_for_item", "major", "several quoted lines answer this line; they are not added up"),
            ("unit_not_convertible", "major", "the quoted unit cannot be converted to the requested unit without an assumption"),
            ("currency_not_convertible", "major", "no exchange rate is recorded for the quote currency"),
            ("price_not_stated", "major" if prio == "mandatory" else "minor", "no price stated"),
            ("price_not_comparable", "major" if prio == "mandatory" else "minor", "a total without the quantity it covers"),
            ("arithmetic_mismatch", "major", "unit price x quantity does not equal the stated total"),
            ("price_outlier_low", "major", "price far below the other quotes; check scope and specification"),
            ("quantity_differs", "minor", "quoted quantity differs from the requested quantity"),
            ("unit_not_stated", "minor", "no unit stated; taken as the requested unit"),
            ("low_confidence", "minor", "value read from a document with low confidence"),
            ("unit_converted", "info", "converted: " + "; ".join(cell["conversions"])),
        ):
            if flag in flags:
                devs.append(_dev(flag, sev, f"Line {n}: {text}.", n))
        if "moq_above_quantity" in flags:
            excess = D(str(cell["moq_excess"]))
            base = D(str(cell["requested_total"]))
            sev = "major" if base == 0 or excess / base > D("0.10") else "minor"
            devs.append(_dev("moq_above_quantity", sev, f"Line {n}: minimum order {cell['moq']:g} exceeds the requested quantity; "
                                                       f"{cell['moq_excess']:,.2f} {ctx['currency']} extra.", n))
        if cell["lead_time_days"] is None and cell["comparable_total"] is not None:
            devs.append(_dev("lead_time_not_stated", "minor", f"Line {n}: no lead time stated.", n))

    for line in quote.get("lines", []):
        if line.get("match_status") != "confirmed" or not line.get("rfq_item_id"):
            devs.append(_dev("extra_line", "info", f"Quoted line '{line.get('description', '')[:60]}' does not answer an RFQ line and is not included."))
    if ctx.get("required_by") and lead is not None:
        arrival = as_of + timedelta(days=float(lead))
        if arrival > ctx["required_by"]:
            late = (arrival - ctx["required_by"]).days
            devs.append(_dev("lead_time_exceeds_required", "major", f"Delivery {late} day(s) after the required date ({ctx['required_by'].isoformat()})."))
    devs.sort(key=lambda d: (SEVERITY_ORDER[d["severity"]], d["line"] or 0, d["code"]))
    return devs


def risk_from(devs: list[dict[str, Any]]) -> dict[str, Any]:
    points = sum(SEVERITY_POINTS[d["severity"]] for d in devs)
    score = min(points, 100)
    level = "high" if score >= 50 else "medium" if score >= 20 else "low"
    counts = {s: sum(1 for d in devs if d["severity"] == s) for s in SEVERITY_POINTS}
    return {"score": score, "level": level, "counts": counts,
            "factors": [f"{d['severity']}: {d['message']} (+{SEVERITY_POINTS[d['severity']]})" for d in devs if SEVERITY_POINTS[d["severity"]]]}


# --- Main ------------------------------------------------------------------------------------------------------------------
def analyse(inputs: dict[str, Any], weights: dict[str, float] | None = None, *, outlier_ratio: float = 0.5,
            fx_max_age_days: int = 30, validity_warning_days: int = 7) -> dict[str, Any]:
    w = normalise_weights(weights)
    currency = inputs["currency"].upper()
    as_of = date.fromisoformat(inputs["as_of"])
    required_by = date.fromisoformat(inputs["required_by"]) if inputs.get("required_by") else None
    items = sorted(inputs["items"], key=lambda i: i["position"])
    suppliers = {s["id"]: s for s in inputs["suppliers"]}
    ctx: dict[str, Any] = {
        "currency": currency, "as_of": as_of, "required_by": required_by, "unit_conversions": inputs.get("unit_conversions") or [],
        "requested_incoterms": inputs.get("requested_incoterms"), "requested_payment_terms": inputs.get("requested_payment_terms"),
        "fx_max_age_days": fx_max_age_days, "validity_warning_days": validity_warning_days, "rates": {},
    }
    for q in inputs["quotes"]:
        ccy = (q.get("currency") or "").upper()
        if ccy and ccy not in ctx["rates"]:
            rate = fx.find_rate(ccy, currency, as_of, inputs.get("fx_rates") or [])
            if rate is not None:
                ctx["rates"][ccy] = rate

    excluded: list[dict[str, Any]] = []
    scored: list[dict[str, Any]] = []
    quoted = {q["supplier_id"] for q in inputs["quotes"]}
    for sid, sup in suppliers.items():
        if sid not in quoted:
            excluded.append({"supplier_id": sid, "supplier_name": sup["name"], "reason": "no_quote", "detail": "No quote received"})
    for q in inputs["quotes"]:
        sup = suppliers.get(q["supplier_id"])
        if sup is None:
            continue
        ccy = (q.get("currency") or "").upper()
        reason = None
        if q["status"] != "verified":
            reason = ("not_verified", "Quote has not been verified by a person")
        elif not ccy:
            reason = ("currency_unknown", "The quote does not state a currency")
        elif ccy not in ctx["rates"]:
            reason = ("no_fx_rate", f"Quoted in {ccy}; no {ccy}/{currency} exchange rate valid on {as_of.isoformat()} is recorded")
        if reason:
            excluded.append({"supplier_id": sup["id"], "supplier_name": sup["name"], "reason": reason[0], "detail": reason[1]})
        else:
            scored.append(dict(q, currency=ccy))

    cells: dict[str, dict[str, dict[str, Any]]] = {q["supplier_id"]: {} for q in scored}
    item_rows: list[dict[str, Any]] = []
    for n, item in enumerate(items, start=1):
        row = {"item_id": item["id"], "line": n, "statement": item["statement"], "quantity": item.get("quantity"),
               "unit": item.get("unit"), "priority": item.get("priority", "unknown"), "cells": {}}
        for q in scored:
            lines = [ln for ln in q["lines"] if ln.get("rfq_item_id") == item["id"] and ln.get("match_status") == "confirmed"]
            cell = normalize_cell(item, lines, q["currency"], ctx)
            row["cells"][q["supplier_id"]] = cell
            cells[q["supplier_id"]][item["id"]] = cell
        prices = [D(str(c["unit_price"])) for c in row["cells"].values() if c["unit_price"] is not None]
        if len(prices) >= 3:
            median = statistics.median(prices)
            for c in row["cells"].values():
                if c["unit_price"] is not None and median > 0 and D(str(c["unit_price"])) < median * (D(1) - D(str(outlier_ratio))):
                    c["flags"].append("price_outlier_low")
        prices_all = sorted(prices)
        row["lowest_unit_price"] = out(prices_all[0]) if prices_all else None
        row["median_unit_price"] = out(statistics.median(prices_all)) if prices_all else None
        item_rows.append(row)

    basket = [i["id"] for i in items if scored and all(cells[q["supplier_id"]][i["id"]]["comparable_total"] is not None for q in scored)]
    weight_total = sum(PRIORITY_WEIGHT.get(i.get("priority", "unknown"), D(1)) for i in items) or D(1)
    global_flags: list[str] = []
    if scored and not basket:
        global_flags.append("No requirement line has a comparable price from every scored supplier, so price is not scored.")
    elif scored and len(basket) < len(items):
        global_flags.append(f"Price is compared on {len(basket)} of {len(items)} lines that every scored supplier priced.")
    inco = {q.get("incoterms") for q in scored if q.get("incoterms")}
    if len(inco) > 1:
        global_flags.append(f"Incoterms differ between quotes ({', '.join(sorted(inco))}); prices may not be like-for-like.")
    converted = sorted({q["currency"] for q in scored if q["currency"] != currency})
    if converted:
        global_flags.append("Converted to " + currency + ": " + "; ".join(ctx["rates"][c].path for c in converted) + ".")

    basket_totals: dict[str, Decimal | None] = {}
    leads: dict[str, Decimal | None] = {}
    moq_excess: dict[str, Decimal] = {}
    for q in scored:
        sid = q["supplier_id"]
        basket_totals[sid] = sum((D(str(cells[sid][i]["comparable_total"])) for i in basket), D(0)) if basket else None
        values = [D(str(c["lead_time_days"])) for c in cells[sid].values() if c["lead_time_days"] is not None and c["comparable_total"] is not None]
        leads[sid] = max(values) if values else None
        moq_excess[sid] = sum((D(str(c["moq_excess"])) for c in cells[sid].values() if c["moq_excess"] is not None), D(0))
    best_basket = min((v for v in basket_totals.values() if v is not None), default=None)
    known_leads = [v for v in leads.values() if v is not None]
    best_lead = min(known_leads) if known_leads else None
    price_active = bool(basket)
    active = {k: v for k, v in w.items() if not (k == "price" and not price_active)}
    active_total = sum(active.values()) or 1.0

    ranking: list[dict[str, Any]] = []
    for q in scored:
        sid, sup = q["supplier_id"], suppliers[q["supplier_id"]]
        crit: dict[str, dict[str, Any]] = {}
        explanation: list[str] = []
        bt = basket_totals[sid]
        if not price_active:
            crit["price"] = {"score": None, "value": None}
            explanation.append("Price: not scored (no line priced by every supplier); its weight was spread over the other criteria.")
        else:
            score = D(1) if bt == 0 and best_basket == 0 else (best_basket / bt if bt else D(0))
            crit["price"] = {"score": out(score, 4), "value": out(bt, 2)}
            explanation.append(f"Price: basket of {len(basket)} line(s) costs {out(bt, 2):,.2f} {currency} (MOQ included); lowest "
                               f"{out(best_basket, 2):,.2f} -> {float(score) * 100:.0f}/100.")
        lead = leads[sid]
        if lead is None:
            crit["lead_time"] = {"score": 0.0, "value": None}
            explanation.append("Lead time: not stated for the priced lines -> 0/100 (not assumed).")
        else:
            score = D(1) if lead == 0 or lead == best_lead else best_lead / lead
            crit["lead_time"] = {"score": out(score, 4), "value": out(lead, 2)}
            explanation.append(f"Lead time: longest priced line {lead.normalize():f} days; fastest {best_lead.normalize():f} -> {float(score) * 100:.0f}/100.")
        covered_w = sum(PRIORITY_WEIGHT.get(i.get("priority", "unknown"), D(1)) for i in items if cells[sid][i["id"]]["comparable_total"] is not None)
        covered = sum(1 for i in items if cells[sid][i["id"]]["comparable_total"] is not None)
        cov = covered_w / weight_total
        crit["coverage"] = {"score": out(cov, 4), "value": covered}
        explanation.append(f"Coverage: {covered} of {len(items)} line(s) comparably priced (mandatory lines count double) -> {float(cov) * 100:.0f}/100.")
        qualified = bool(sup.get("qualified"))
        crit["qualification"] = {"score": 1.0 if qualified else 0.0, "value": qualified}
        explanation.append("Qualification: qualified -> 100/100." if qualified else "Qualification: not currently qualified -> 0/100.")
        devs = detect_deviations(q, sup, items, cells[sid], ctx, lead)
        risk = risk_from(devs)
        crit["risk"] = {"score": round(1 - risk["score"] / 100, 4), "value": risk["score"]}
        explanation.append(f"Risk: {risk['score']} point(s) from {len([d for d in devs if d['severity'] != 'info'])} deviation(s), "
                           f"{risk['level']} -> {100 - risk['score']}/100.")

        total = sum(active[k] * (crit[k]["score"] or 0.0) for k in active) / active_total * 100
        flags = sorted({d["code"] for d in devs if d["severity"] != "info"})
        on_time = None
        slack = None
        if required_by and lead is not None:
            slack = (required_by - as_of).days - float(lead)
            on_time = slack >= 0
        ranking.append({
            "supplier_id": sid, "supplier_name": sup["name"], "supplier_code": sup.get("code", ""), "quote_id": q["id"],
            "score": round(total, 2), "criteria": crit, "basket_total": out(bt, 2) if bt is not None else None,
            "lead_time_days": out(lead, 2) if lead is not None else None, "coverage": covered, "qualified": qualified,
            "incoterms": q.get("incoterms"), "payment_terms": q.get("payment_terms"), "valid_until": q.get("valid_until"),
            "currency": q["currency"], "moq_excess_total": out(moq_excess[sid], 2), "on_time": on_time,
            "slack_days": round(slack, 2) if slack is not None else None, "risk": risk, "deviations": devs,
            "flags": flags, "explanation": explanation,
        })

    ranking.sort(key=lambda r: (-r["score"], r["supplier_name"].lower()))
    for n, r in enumerate(ranking, start=1):
        r["rank"] = n
    if len(ranking) > 1 and ranking[0]["score"] - ranking[1]["score"] < 2:
        global_flags.append("The top two scores are within 2 points; treat them as equivalent.")
    excluded.sort(key=lambda e: e["supplier_name"].lower())
    lead_values = [D(str(r["lead_time_days"])) for r in ranking if r["lead_time_days"] is not None]
    return {
        "engine_version": ENGINE_VERSION, "currency": currency, "as_of": inputs["as_of"], "weights": w,
        "active_weights": {k: round(v / active_total, 6) for k, v in active.items()},
        "basket_item_ids": basket, "items": item_rows, "ranking": ranking, "excluded": excluded, "global_flags": global_flags,
        "lead_time_analysis": {
            "required_by": inputs.get("required_by"),
            "available_days": (required_by - as_of).days if required_by else None,
            "fastest": out(min(lead_values), 2) if lead_values else None,
            "median": out(statistics.median(lead_values), 2) if lead_values else None,
            "slowest": out(max(lead_values), 2) if lead_values else None,
            "late_suppliers": [r["supplier_name"] for r in ranking if r["on_time"] is False],
        },
        "moq_analysis": {
            "suppliers_with_excess": [{"supplier_name": r["supplier_name"], "excess": r["moq_excess_total"]} for r in ranking if r["moq_excess_total"]],
            "lines": [{"line": it["line"], "supplier_id": sid, "moq": c["moq"], "excess": c["moq_excess"]}
                      for it in item_rows for sid, c in it["cells"].items() if c["moq_excess"]],
        },
        "fx_used": {c: {"rate": float(r.rate), "valid_from": r.valid_from.isoformat(), "path": r.path, "age_days": r.age_days}
                    for c, r in sorted(ctx["rates"].items()) if c != currency},
        "highest_score_supplier_id": ranking[0]["supplier_id"] if ranking else None,
        "decision": "human", "decision_note": DECISION_NOTE, "inputs_sha256": inputs_hash(inputs),
    }
