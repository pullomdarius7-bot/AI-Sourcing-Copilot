"""Guardrails around model calls: input hygiene, output validation, grounding against the source."""
import re
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.ai.schemas import INCOTERMS, Analysis, NumField, StrField

REVIEW_PREFIXES = (
    "ungrounded", "arithmetic_mismatch", "possible_prompt_injection", "invalid_incoterm",
    "truncated_input", "low_confidence",
)
_INJECTION = [
    r"ignore (all |any )?(the )?(previous|prior|above) (instructions|prompts?)",
    r"disregard (all |any )?(the )?(previous|prior|above)",
    r"you are now\b",
    r"(reveal|print|show) (your |the )?(system )?prompt",
    r"new instructions\s*:",
    r"</?\s*(system|assistant|document)[^>]*>",
]
_INJECTION_RE = re.compile("|".join(_INJECTION), re.IGNORECASE)
_CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£", "JPY": "¥", "INR": "₹"}


class OutputInvalid(Exception):
    def __init__(self, errors: str) -> None:
        super().__init__(errors)
        self.errors = errors


def norm(text: str) -> str:
    text = text.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    return re.sub(r"\s+", " ", text).strip().lower()


def _luhn(digits: str) -> bool:
    total, flip = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if flip:
            d *= 2
            d -= 9 if d > 9 else 0
        total += d
        flip = not flip
    return total % 10 == 0


@dataclass
class Prepared:
    text: str
    flags: list[str]


def prepare_text(text: str, max_chars: int) -> Prepared:
    flags: list[str] = []
    if _INJECTION_RE.search(text):
        flags.append("possible_prompt_injection")
    if len(text) > max_chars:
        text = text[:max_chars]
        flags.append("truncated_input")
    return Prepared(text, flags)


def redact(text: str) -> tuple[str, list[str]]:
    """Strip payment-card and SSN patterns before text leaves for an external provider."""
    counts = {"card_number": 0, "ssn": 0}

    def card(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        if 13 <= len(digits) <= 19 and _luhn(digits):
            counts["card_number"] += 1
            return "[REDACTED-CARD]"
        return match.group(0)

    def ssn(_: re.Match[str]) -> str:
        counts["ssn"] += 1
        return "[REDACTED-SSN]"

    text = _CARD_RE.sub(card, text)
    text = _SSN_RE.sub(ssn, text)
    return text, [f"redacted:{k}={v}" for k, v in counts.items() if v]


def validate_output(raw: Any) -> Analysis:
    if not isinstance(raw, dict):
        raise OutputInvalid("The answer must be a JSON object")
    try:
        return Analysis.model_validate(raw)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:8])
        raise OutputInvalid(problems) from exc


def _number_variants(value: float) -> set[str]:
    out = {f"{value:g}", f"{value:.2f}", f"{value:,.2f}"}
    if value == int(value):
        out |= {f"{value:,.0f}", f"{value:.0f}"}
    swapped = {v.replace(",", "\0").replace(".", ",").replace("\0", ".") for v in out}
    spaced = {v.replace(",", " ") for v in out}
    return {v for v in out | swapped | spaced if v}


def _number_in(text_norm: str, value: float) -> bool:
    for variant in _number_variants(value):
        if re.search(rf"(?<![\d.,]){re.escape(variant)}(?![\d])", text_norm):
            return True
    return False


def _reset(field: StrField | NumField) -> None:
    field.value, field.unknown, field.confidence, field.source = None, True, 0.0, None


def _ground(
    field: StrField | NumField, name: str, source_norm: str, flags: list[str], context: str = ""
) -> None:
    field.verified = False  # only humans verify
    if field.unknown or field.value is None:
        _reset(field)
        return
    quote = norm(field.source or "")
    if not quote or quote not in source_norm:
        flags.append(f"ungrounded_source:{name}")
        _reset(field)
        return
    haystack = f"{quote} {context}"
    if isinstance(field.value, str):
        value_norm = norm(field.value)
        symbol = _SYMBOLS.get(field.value.upper(), "\0") if name == "currency" else "\0"
        if value_norm not in haystack and symbol not in haystack:
            flags.append(f"ungrounded_value:{name}")
            _reset(field)
    elif not _number_in(haystack, float(field.value)):
        flags.append(f"ungrounded_value:{name}")
        _reset(field)


def apply_guardrails(analysis: Analysis, source_text: str) -> tuple[dict[str, Any], list[str]]:
    """Ground every value in the document; anything unsupported becomes explicit 'unknown'."""
    flags: list[str] = []
    source_norm = norm(source_text)

    for name in ("supplier_name", "currency", "incoterms", "payment_terms", "valid_until"):
        _ground(getattr(analysis, name), name, source_norm, flags)

    currency = analysis.currency
    if currency.value is not None:
        code = currency.value.strip().upper()
        if re.fullmatch(r"[A-Z]{3}", code):
            currency.value = code
        else:
            flags.append("ungrounded_value:currency")
            _reset(currency)
    terms = analysis.incoterms
    if terms.value is not None:
        token = terms.value.strip().split(" ")[0].upper()
        if token in INCOTERMS:
            terms.value = token
        else:
            flags.append("invalid_incoterm")
            _reset(terms)

    kept = []
    for index, item in enumerate(analysis.line_items):
        item_norm = norm(item.source)
        if item_norm not in source_norm:
            flags.append(f"ungrounded_line_item:{index}")
            continue
        for fname in ("quantity", "unit", "unit_price", "total_price", "lead_time_days"):
            _ground(getattr(item, fname), f"line_items[{index}].{fname}", source_norm, flags, item_norm)
        q, u, t = item.quantity.value, item.unit_price.value, item.total_price.value
        if q is not None and u is not None and t is not None and t > 0 and abs(q * u - t) / t > 0.01:
            flags.append(f"arithmetic_mismatch:{index}")
        kept.append(item)
    analysis.line_items = kept

    analysis.overall_confidence = round(min(max(analysis.overall_confidence, 0.0), 1.0), 3)
    if analysis.overall_confidence < 0.5:
        flags.append("low_confidence")

    result = analysis.model_dump()
    missing = [n for n in ("supplier_name", "currency", "incoterms", "payment_terms", "valid_until") if result[n]["unknown"]]
    for i, it in enumerate(result["line_items"]):
        missing += [f"line_items[{i}].{n}" for n in ("quantity", "unit_price", "total_price", "lead_time_days") if it[n]["unknown"]]
    result["missing_fields"] = missing
    return result, flags


def needs_review(flags: list[str]) -> bool:
    return any(f.startswith(REVIEW_PREFIXES) for f in flags)
