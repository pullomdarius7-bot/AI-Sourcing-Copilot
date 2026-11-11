"""Canonical RFQ document: the single source for the PDF, the email package, the JSON export and the approval hash."""
import hashlib
import json
from typing import Any

PRIORITY_LABEL = {"mandatory": "Mandatory", "preferred": "Preferred", "optional": "Optional", "unknown": "Not stated"}
QUALIFIER_SYMBOL = {"min": "≥ ", "max": "≤ ", "approx": "≈ ", "exact": ""}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_of(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def format_number(value: float) -> str:
    return f"{value:,.6f}".rstrip("0").rstrip(".") if value != int(value) else f"{int(value):,}"


def parameter_text(param: dict[str, Any]) -> str:
    """Human-readable parameter value. Unknown values are never filled in."""
    label = str(param.get("name", "")).replace("_", " ")
    if param.get("unknown"):
        return f"{label}: not specified – please state"
    value = param.get("value")
    if isinstance(value, int | float) and not isinstance(value, bool):
        text = format_number(float(value))
    else:
        text = str(value)
    prefix = QUALIFIER_SYMBOL.get(param.get("qualifier") or "", "")
    unit = f" {param['unit']}" if param.get("unit") else ""
    return f"{label}: {prefix}{text}{unit}"


def build_document(
    *, number: str, title: str, version: int, currency: str, response_due_date: str | None, message: str | None,
    organization: str, category: str | None, items: list[dict[str, Any]], required_by: str | None = None,
    incoterms: str | None = None, payment_terms: str | None = None,
) -> dict[str, Any]:
    """Items are dicts with position, statement, category, priority, quantity, unit, parameters.

    Requested delivery date and terms are included only when set, so RFQs approved before they existed keep their hash."""
    ordered = sorted(items, key=lambda i: (i["position"], i["statement"]))
    terms = {k: v for k, v in (("required_by", required_by), ("incoterms", incoterms), ("payment_terms", payment_terms)) if v}
    doc = {
        "schema": "ai-sourcing-copilot/rfq@1",
        "number": number,
        "title": title,
        "version": version,
        "currency": currency,
        "response_due_date": response_due_date,
        "message": message or "",
        "organization": organization,
        "category": category,
        "items": [
            {
                "line": n,
                "statement": i["statement"],
                "category": i.get("category") or "other",
                "priority": i.get("priority") or "unknown",
                "quantity": i.get("quantity"),
                "unit": i.get("unit"),
                "parameters": [
                    {k: p.get(k) for k in ("name", "value", "unit", "qualifier", "unknown")}
                    for p in i.get("parameters") or []
                ],
            }
            for n, i in enumerate(ordered, start=1)
        ],
    }
    if terms:
        doc["requested_terms"] = terms
    return doc


def approval_hash(document: dict[str, Any], supplier_ids: list[str]) -> str:
    """What an approver approves: the exact content and exactly who receives it."""
    return sha256_of({"document": document, "suppliers": sorted(supplier_ids)})
