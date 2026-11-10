"""Deterministic, explainable quote comparison.

Rules (zero-assumption):
- Only verified quotes are scored; anything else is listed under "excluded" with the reason.
- No currency conversion: a quote in another currency than the RFQ is not price-comparable and is excluded.
- No unit conversion: a line in a different unit than requested is not price-comparable on that line.
- Only lines a person confirmed as answering an RFQ line are used.
- Missing information is never filled in: an unknown criterion scores 0 and the explanation says so.
- Prices are compared on the basket of lines every scored supplier priced, so totals are like-for-like.
- The output ranks and explains; it never decides. Awarding is a separate, recorded human decision.
"""
import statistics
from datetime import date
from typing import Any

from app.rfqs.content import format_number, sha256_of

DEFAULT_WEIGHTS = {"price": 0.5, "lead_time": 0.2, "coverage": 0.2, "qualification": 0.1}
PRIORITY_WEIGHT = {"mandatory": 2.0, "preferred": 1.0, "optional": 0.5, "unknown": 1.0}
UNIT_ALIASES = {
    "pc": "pcs", "piece": "pcs", "pieces": "pcs", "pcs.": "pcs", "ea": "pcs", "each": "pcs", "stk": "pcs", "units": "pcs",
    "unit": "pcs", "kgs": "kg", "kilogram": "kg", "kilograms": "kg", "metre": "m", "meter": "m", "meters": "m",
    "metres": "m", "litre": "l", "liter": "l", "litres": "l", "liters": "l",
}
DECISION_NOTE = (
    "This ranking is decision support only. It uses verified quote data and the weights shown; it does not award. "
    "The award is a separate decision recorded with its reason."
)


class ComparisonError(ValueError):
    pass


def normalise_weights(weights: dict[str, float] | None) -> dict[str, float]:
    raw = dict(DEFAULT_WEIGHTS if weights is None else weights)
    unknown = set(raw) - set(DEFAULT_WEIGHTS)
    if unknown:
        raise ComparisonError(f"Unknown criteria: {', '.join(sorted(unknown))}")
    if any(v < 0 for v in raw.values()):
        raise ComparisonError("Weights cannot be negative")
    full = {k: float(raw.get(k, 0.0)) for k in DEFAULT_WEIGHTS}
    total = sum(full.values())
    if total <= 0:
        raise ComparisonError("At least one weight must be positive")
    return {k: round(v / total, 6) for k, v in full.items()}


def norm_unit(unit: str | None) -> str | None:
    if unit is None or not str(unit).strip():
        return None
    u = str(unit).strip().lower()
    return UNIT_ALIASES.get(u, u)


def inputs_hash(inputs: dict[str, Any]) -> str:
    """Fingerprint of the data a comparison used (weights excluded), to detect stale comparisons."""
    return sha256_of({k: inputs[k] for k in ("currency", "items", "suppliers", "quotes", "as_of")})


def _cell(item: dict[str, Any], lines: list[dict[str, Any]]) -> dict[str, Any]:
    flags: list[str] = []
    if not lines:
        return {"unit_price": None, "comparable_total": None, "lead_time_days": None, "flags": ["not_quoted"], "confidence": None}
    if len(lines) > 1:
        return {"unit_price": None, "comparable_total": None, "lead_time_days": None,
                "flags": ["multiple_lines_for_item"], "confidence": None}
    line = lines[0]
    unit_price, total = line.get("unit_price"), line.get("total_price")
    qty_req, qty_line = item.get("quantity"), line.get("quantity")
    req_unit, line_unit = norm_unit(item.get("unit")), norm_unit(line.get("unit"))
    comparable = None
    if req_unit and line_unit and req_unit != line_unit:
        flags.append("unit_mismatch")
    elif qty_req is None:
        per_unit = unit_price if unit_price is not None else (total / qty_line if total is not None and qty_line else None)
        if per_unit is None:
            flags.append("price_not_stated" if unit_price is None and total is None else "price_not_comparable")
        else:
            comparable = per_unit
            flags.append("requested_quantity_unknown")  # every supplier is compared per unit on this line
    elif unit_price is not None:
        comparable = unit_price * qty_req
        if qty_line is not None and abs(qty_line - qty_req) > 1e-9:
            flags.append("quantity_differs")
    elif total is not None and qty_line:
        comparable = total if abs(qty_line - qty_req) <= 1e-9 else total / qty_line * qty_req
        if abs(qty_line - qty_req) > 1e-9:
            flags.append("quantity_differs")
    elif total is not None:
        flags.append("price_not_comparable")  # a total without its quantity cannot be put on the same basis
    else:
        flags.append("price_not_stated")
    if unit_price is not None and qty_line is not None and total is not None and total > 0:
        if abs(unit_price * qty_line - total) / total > 0.01:
            flags.append("arithmetic_mismatch")
    if line.get("confidence", 1.0) < 0.6:
        flags.append("low_confidence")
    return {
        "unit_price": unit_price, "comparable_total": round(comparable, 6) if comparable is not None else None,
        "lead_time_days": line.get("lead_time_days"), "flags": flags, "confidence": line.get("confidence", 1.0),
    }


def compare(inputs: dict[str, Any], weights: dict[str, float] | None = None, outlier_ratio: float = 0.5) -> dict[str, Any]:
    """inputs: {currency, as_of, items[], suppliers[], quotes[]} (see tests/test_comparison.py for the shape)."""
    w = normalise_weights(weights)
    currency = inputs["currency"]
    as_of = date.fromisoformat(inputs["as_of"])
    items = sorted(inputs["items"], key=lambda i: i["position"])
    suppliers = {s["id"]: s for s in inputs["suppliers"]}
    excluded: list[dict[str, Any]] = []
    scored_quotes: list[dict[str, Any]] = []
    global_flags: list[str] = []

    quoted = {q["supplier_id"] for q in inputs["quotes"]}
    for sid, sup in suppliers.items():
        if sid not in quoted:
            excluded.append({"supplier_id": sid, "supplier_name": sup["name"], "reason": "no_quote", "detail": "No quote received"})
    for q in inputs["quotes"]:
        sup = suppliers.get(q["supplier_id"])
        if sup is None:
            continue
        if q["status"] != "verified":
            excluded.append({"supplier_id": sup["id"], "supplier_name": sup["name"], "reason": "not_verified",
                             "detail": "Quote has not been verified by a person"})
        elif not q.get("currency"):
            excluded.append({"supplier_id": sup["id"], "supplier_name": sup["name"], "reason": "currency_unknown",
                             "detail": "The quote does not state a currency"})
        elif q["currency"] != currency:
            excluded.append({"supplier_id": sup["id"], "supplier_name": sup["name"], "reason": "currency_mismatch",
                             "detail": f"Quoted in {q['currency']}, the RFQ asks for {currency}; no conversion is assumed"})
        else:
            scored_quotes.append(q)

    item_rows: list[dict[str, Any]] = []
    cells: dict[str, dict[str, dict[str, Any]]] = {q["supplier_id"]: {} for q in scored_quotes}
    for n, item in enumerate(items, start=1):
        row = {"item_id": item["id"], "line": n, "statement": item["statement"], "quantity": item.get("quantity"),
               "unit": item.get("unit"), "priority": item.get("priority", "unknown"), "cells": {}}
        for q in scored_quotes:
            lines = [ln for ln in q["lines"] if ln.get("rfq_item_id") == item["id"] and ln.get("match_status") == "confirmed"]
            cell = _cell(item, lines)
            row["cells"][q["supplier_id"]] = cell
            cells[q["supplier_id"]][item["id"]] = cell
        prices = [c["unit_price"] for c in row["cells"].values() if c["unit_price"] is not None and "unit_mismatch" not in c["flags"]]
        if len(prices) >= 3:
            median = statistics.median(prices)
            for c in row["cells"].values():
                if c["unit_price"] is not None and median > 0 and c["unit_price"] < median * (1 - outlier_ratio):
                    c["flags"].append("price_outlier_low")
        item_rows.append(row)

    basket = [
        i["id"] for i in items
        if scored_quotes and all(cells[q["supplier_id"]][i["id"]]["comparable_total"] is not None for q in scored_quotes)
    ]
    weight_total = sum(PRIORITY_WEIGHT.get(i.get("priority", "unknown"), 1.0) for i in items) or 1.0
    inco = {q.get("incoterms") for q in scored_quotes if q.get("incoterms")}
    if len(inco) > 1:
        global_flags.append(f"Incoterms differ between quotes ({', '.join(sorted(inco))}); prices may not be like-for-like.")
    if scored_quotes and not basket:
        global_flags.append("No requirement line was priced by every scored supplier, so price is not scored.")
    elif scored_quotes and len(basket) < len(items):
        global_flags.append(f"Price is compared on {len(basket)} of {len(items)} lines that every scored supplier priced.")

    basket_totals = {q["supplier_id"]: (sum(cells[q["supplier_id"]][i]["comparable_total"] for i in basket) if basket else None)
                     for q in scored_quotes}
    leads: dict[str, float | None] = {}
    for q in scored_quotes:
        values = [c["lead_time_days"] for c in cells[q["supplier_id"]].values()
                  if c["lead_time_days"] is not None and c["comparable_total"] is not None]
        leads[q["supplier_id"]] = max(values) if values else None
    best_basket = min((v for v in basket_totals.values() if v is not None), default=None)
    known_leads = [v for v in leads.values() if v is not None]
    best_lead = min(known_leads) if known_leads else None
    price_active = bool(basket)
    active = {k: v for k, v in w.items() if not (k == "price" and not price_active)}
    active_total = sum(active.values()) or 1.0

    ranking: list[dict[str, Any]] = []
    for q in scored_quotes:
        sid = q["supplier_id"]
        sup = suppliers[sid]
        flags: list[str] = []
        explanation: list[str] = []
        crit: dict[str, dict[str, Any]] = {}

        bt = basket_totals[sid]
        if not price_active:
            crit["price"] = {"score": None, "value": None}
            explanation.append("Price: not scored (no common priced lines); its weight was spread over the other criteria.")
        elif best_basket == 0 and bt == 0:
            crit["price"] = {"score": 1.0, "value": bt}
            explanation.append("Price: basket total 0.")
        else:
            score = (best_basket / bt) if bt else 0.0
            crit["price"] = {"score": round(score, 4), "value": bt}
            explanation.append(f"Price: basket of {len(basket)} line(s) totals {format_number(bt)} {currency}; "
                               f"lowest is {format_number(best_basket)} -> {score * 100:.0f}/100.")

        lead = leads[sid]
        if lead is None:
            crit["lead_time"] = {"score": 0.0, "value": None}
            flags.append("lead_time_unknown")
            explanation.append("Lead time: not stated for the priced lines -> 0/100 (not assumed).")
        else:
            score = 1.0 if lead == 0 or best_lead == lead else (best_lead / lead if lead else 1.0)
            crit["lead_time"] = {"score": round(score, 4), "value": lead}
            explanation.append(f"Lead time: longest priced line {format_number(lead)} days; fastest is "
                               f"{format_number(best_lead)} -> {score * 100:.0f}/100.")

        covered_weight = sum(PRIORITY_WEIGHT.get(i.get("priority", "unknown"), 1.0) for i in items
                             if cells[sid][i["id"]]["comparable_total"] is not None)
        covered = sum(1 for i in items if cells[sid][i["id"]]["comparable_total"] is not None)
        missing_mandatory = [n for n, i in enumerate(items, start=1)
                             if i.get("priority") == "mandatory" and cells[sid][i["id"]]["comparable_total"] is None]
        cov = covered_weight / weight_total
        crit["coverage"] = {"score": round(cov, 4), "value": covered}
        explanation.append(f"Coverage: priced {covered} of {len(items)} line(s) (mandatory lines count double) -> {cov * 100:.0f}/100.")
        if missing_mandatory:
            flags.append("mandatory_lines_missing")
            explanation.append("Mandatory line(s) without a comparable price: " + ", ".join(map(str, missing_mandatory)) + ".")

        qualified = bool(sup.get("qualified"))
        crit["qualification"] = {"score": 1.0 if qualified else 0.0, "value": qualified}
        explanation.append("Qualification: supplier is qualified -> 100/100." if qualified
                           else "Qualification: supplier is not currently qualified -> 0/100.")
        if not qualified:
            flags.append("supplier_not_qualified")

        for key, label in (("incoterms", "incoterms_unknown"), ("payment_terms", "payment_terms_unknown"),
                           ("valid_until", "validity_unknown")):
            if not q.get(key):
                flags.append(label)
        if q.get("valid_until") and date.fromisoformat(q["valid_until"]) < as_of:
            flags.append("quote_expired")
        for c in cells[sid].values():
            for f in c["flags"]:
                if f not in ("not_quoted", "requested_quantity_unknown") and f not in flags:
                    flags.append(f)

        total = sum(active[k] * (crit[k]["score"] or 0.0) for k in active) / active_total * 100
        ranking.append({
            "supplier_id": sid, "supplier_name": sup["name"], "supplier_code": sup.get("code", ""),
            "quote_id": q["id"], "score": round(total, 2), "criteria": crit, "basket_total": bt,
            "lead_time_days": lead, "coverage": covered, "qualified": qualified, "incoterms": q.get("incoterms"),
            "payment_terms": q.get("payment_terms"), "valid_until": q.get("valid_until"), "flags": flags,
            "explanation": explanation,
        })

    ranking.sort(key=lambda r: (-r["score"], r["supplier_name"].lower()))
    for n, r in enumerate(ranking, start=1):
        r["rank"] = n
    if len(ranking) > 1 and ranking[0]["score"] - ranking[1]["score"] < 2:
        global_flags.append("The top two scores are within 2 points; treat them as equivalent.")
    excluded.sort(key=lambda e: e["supplier_name"].lower())

    return {
        "currency": currency, "as_of": inputs["as_of"], "weights": w,
        "active_weights": {k: round(v / active_total, 6) for k, v in active.items()},
        "basket_item_ids": basket, "items": item_rows, "ranking": ranking, "excluded": excluded,
        "global_flags": global_flags, "highest_score_supplier_id": ranking[0]["supplier_id"] if ranking else None,
        "decision": "human", "decision_note": DECISION_NOTE, "inputs_sha256": inputs_hash(inputs),
    }
