"""Deterministic grounding, validation and confidence scoring of model output.

Nothing a model says is trusted. A value survives only if (1) its source quote is found in the document,
(2) the value itself appears in that quote, and (3) every claim (unit, priority, paraphrase) is supported by
the quote. Anything else becomes an explicit unknown. Offsets always refer to the document's extracted text.
"""
import bisect
import re
from dataclasses import dataclass, field
from typing import Any

from app.ai.guardrails import _number_in
from app.requirements.schemas import NAME_RE, RawExtraction, RawParameter, RawRequirement

SINGLETONS = {
    "delivery_date", "incoterms", "payment_terms", "currency", "delivery_location", "warranty_period",
    "lead_time", "validity",
}
HEDGE_RE = re.compile(
    r"\b(approx(?:imately|\.)?|about|around|typically|usually|generally|might|could|possibly|tbd|tbc|"
    r"to be (?:determined|confirmed)|preferably|ideally|or equivalent|or similar|if possible|"
    r"where possible|subject to)\b"
)
MANDATORY_RE = re.compile(r"\b(shall|must|required|requires?|mandatory|needs?|has to|have to|essential)\b")
PREFERRED_RE = re.compile(r"\b(should|preferred|preferably|desirable|desired|ideally|recommended)\b")
OPTIONAL_RE = re.compile(r"\b(may|optional|optionally|if possible|nice to have)\b")
_STOPWORDS = {
    "that", "this", "with", "from", "have", "must", "shall", "should", "will", "been", "were", "they",
    "their", "which", "when", "than", "then", "into", "also", "such", "each", "any", "all", "the", "and",
    "for", "are", "not", "per", "can", "may",
}


# --- Normalisation with offset mapping ----------------------------------------------
def _norm_chars(ch: str) -> str:
    ch = ch.lower()
    if ch in ("\u2018", "\u2019", "\u02bc"):
        return "'"
    if ch in ("\u201c", "\u201d"):
        return '"'
    if ch in ("\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2212"):
        return "-"
    if ch == "\u00a0":
        return " "
    if ch in ("\u200b", "\u00ad", "\ufeff"):
        return ""
    return ch


def normalize(text: str) -> str:
    out: list[str] = []
    prev_space = True
    for ch in text:
        for c in _norm_chars(ch):
            if c.isspace():
                if not prev_space:
                    out.append(" ")
                prev_space = True
            else:
                out.append(c)
                prev_space = False
    return "".join(out).strip()


@dataclass
class Located:
    start: int
    end: int
    method: str  # exact | normalized


class TextIndex:
    def __init__(self, text: str) -> None:
        self.text = text
        chars: list[str] = []
        self.map: list[int] = []
        prev_space = True
        for i, ch in enumerate(text):
            for c in _norm_chars(ch):
                if c.isspace():
                    if prev_space:
                        continue
                    chars.append(" ")
                    prev_space = True
                else:
                    chars.append(c)
                    prev_space = False
                self.map.append(i)
        self.norm = "".join(chars)
        self._newlines = [m.start() for m in re.finditer("\n", text)]
        self._sections = [(m.start(), m.group(1).strip()) for m in re.finditer(r"^## Sheet: (.+)$", text, re.M)]

    def locate(self, quote: str, hint: int = 0) -> Located | None:
        q = quote.strip()
        if len(q) < 3:
            return None
        pos = self.text.find(q, hint)
        if pos < 0 and hint > 0:
            pos = self.text.find(q)
        if pos >= 0:
            return Located(pos, pos + len(q), "exact")
        nq = normalize(q)
        if len(nq) < 3:
            return None
        start_norm = bisect.bisect_left(self.map, hint)
        i = self.norm.find(nq, start_norm)
        if i < 0 and start_norm > 0:
            i = self.norm.find(nq)
        if i < 0:
            return None
        return Located(self.map[i], self.map[i + len(nq) - 1] + 1, "normalized")

    def line_of(self, offset: int) -> int:
        return bisect.bisect_left(self._newlines, offset) + 1

    def section_of(self, offset: int) -> str | None:
        found = None
        for start, name in self._sections:
            if start <= offset:
                found = name
            else:
                break
        return found


# --- Result containers -------------------------------------------------------------------
@dataclass
class GParam:
    name: str
    value_type: str
    text: str | None = None
    number: float | None = None
    unit: str | None = None
    qualifier: str | None = None
    unknown: bool = True
    model_confidence: float = 0.0
    confidence: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)
    quote: str | None = None
    start: int | None = None
    end: int | None = None
    line: int | None = None
    section: str | None = None
    grounding: str = "unknown"
    flags: list[str] = field(default_factory=list)

    def value_key(self) -> tuple:
        if self.number is not None:
            return (self.name, round(self.number, 6), normalize(self.unit or ""))
        return (self.name, normalize(self.text or ""))


@dataclass
class GReq:
    statement: str
    category: str
    priority: str
    quote: str
    start: int
    end: int
    line: int
    section: str | None
    model_confidence: float
    confidence: float
    detail: dict[str, Any] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    params: list[GParam] = field(default_factory=list)


@dataclass
class GUnspecified:
    name: str
    reason: str


@dataclass
class GroundedExtraction:
    requirements: list[GReq]
    unknowns: list[GParam]
    flags: list[dict[str, Any]]


# --- Scoring -----------------------------------------------------------------------------------
def clamp(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def score(
    model_conf: float, method: str, quote: str, *, unit_missing: bool = False,
    outside_requirement: bool = False, extra: dict[str, float] | None = None,
) -> tuple[float, dict[str, Any], list[str]]:
    flags: list[str] = []
    grounding = 1.0 if method == "exact" else 0.95
    hedging = 1.0
    if HEDGE_RE.search(normalize(quote)):
        hedging = 0.7
        flags.append("hedged_language")
    length = 1.0
    if len(quote.strip()) < 6:
        length = 0.8
        flags.append("short_quote")
    elif len(quote) > 400:
        length = 0.9
        flags.append("long_quote")
    unit = 0.85 if unit_missing else 1.0
    context = 0.9 if outside_requirement else 1.0
    final = clamp(model_conf) * grounding * hedging * length * unit * context
    detail = {
        "model": round(clamp(model_conf), 3), "grounding": grounding, "hedging": hedging,
        "length": length, "unit": unit, "context": context,
    }
    for key, factor in (extra or {}).items():
        final *= factor
        detail[key] = factor
    detail["final"] = round(final, 3)
    return round(final, 3), detail, flags


def bucket(confidence: float) -> str:
    return "high" if confidence >= 0.85 else "medium" if confidence >= 0.6 else "low"


# --- Validators ------------------------------------------------------------------------------------
def statement_supported(statement: str, quote: str) -> bool:
    """A paraphrase is only kept if its numbers and most of its content words come from the quote."""
    nq = normalize(quote)
    ns = normalize(statement)
    for number in re.findall(r"\d+(?:[.,]\d+)?", ns):
        if number not in nq:
            return False
    words = [w for w in re.findall(r"[a-z0-9][a-z0-9\-/]{3,}", ns) if w not in _STOPWORDS]
    if not words:
        return True
    supported = sum(1 for w in words if w in nq)
    return supported / len(words) >= 0.7


def validate_priority(priority: str, quote: str) -> tuple[str, str | None]:
    nq = normalize(quote)
    pattern = {"mandatory": MANDATORY_RE, "preferred": PREFERRED_RE, "optional": OPTIONAL_RE}.get(priority)
    if pattern is None or pattern.search(nq):
        return priority, None
    return "unknown", "priority_not_supported"


def _unknown(name: str, value_type: str, reason: str, discarded: dict[str, Any] | None = None, flag: str | None = None) -> GParam:
    detail: dict[str, Any] = {"reason": reason}
    if discarded:
        detail["discarded_model_claim"] = discarded  # kept for audit only; never shown as a value
    return GParam(name=name, value_type=value_type, flags=[flag] if flag else [], detail=detail)


def ground_param(raw: RawParameter, index: TextIndex, hint: int, req_span: tuple[int, int]) -> GParam:
    if raw.unknown or (raw.text is None and raw.number is None):
        return _unknown(raw.name, raw.value_type, "Not stated in the document")
    claim = {"text": raw.text, "number": raw.number, "unit": raw.unit, "source": raw.source}
    if raw.value_type == "text" and not (raw.text and raw.text.strip()):
        return _unknown(raw.name, raw.value_type, "No value given", claim, "missing_value")
    if raw.value_type == "number" and raw.number is None:
        return _unknown(raw.name, raw.value_type, "No value given", claim, "missing_value")
    if not raw.source:
        return _unknown(raw.name, raw.value_type, "No source quote given", claim, "ungrounded_source")
    located = index.locate(raw.source, hint)
    if located is None:
        return _unknown(raw.name, raw.value_type, "Quote not found in the document", claim, "ungrounded_source")
    quote = index.text[located.start : located.end]
    nq = normalize(quote)

    if raw.value_type == "number":
        if not _number_in(nq, abs(float(raw.number))):  # type: ignore[arg-type]
            return _unknown(raw.name, raw.value_type, "Number does not appear in its quote", claim, "value_not_in_source")
    elif normalize(raw.text or "") not in nq:  # type: ignore[arg-type]
        return _unknown(raw.name, raw.value_type, "Value does not appear in its quote", claim, "value_not_in_source")

    flags: list[str] = []
    unit = raw.unit.strip() if raw.unit and raw.unit.strip() else None
    unit_missing = False
    if unit and normalize(unit) not in nq:
        flags.append("unit_not_in_source")
        unit, unit_missing = None, True
    outside = not (req_span[0] <= located.start and located.end <= req_span[1])
    if outside:
        flags.append("outside_requirement_quote")
    confidence, detail, score_flags = score(
        raw.confidence, located.method, quote, unit_missing=unit_missing, outside_requirement=outside
    )
    return GParam(
        name=raw.name, value_type=raw.value_type, text=raw.text.strip() if raw.text else None,
        number=raw.number, unit=unit, qualifier=raw.qualifier, unknown=False,
        model_confidence=clamp(raw.confidence), confidence=confidence, detail=detail, quote=quote,
        start=located.start, end=located.end, line=index.line_of(located.start),
        section=index.section_of(located.start), grounding="grounded", flags=flags + score_flags,
    )


def ground_requirement(raw: RawRequirement, index: TextIndex, hint: int, run_flags: list[dict[str, Any]]) -> GReq | None:
    located = index.locate(raw.source, hint)
    if located is None:
        run_flags.append({"code": "dropped_requirement", "reason": "source_not_found", "statement": raw.statement[:120]})
        return None
    quote = index.text[located.start : located.end]
    flags: list[str] = []
    extra: dict[str, float] = {}
    statement = raw.statement.strip()
    if not statement_supported(statement, quote):
        statement = re.sub(r"\s+", " ", quote).strip()[:600]
        flags.append("statement_replaced_with_quote")
        extra["statement"] = 0.9
    priority, priority_flag = validate_priority(raw.priority, quote)
    if priority_flag:
        flags.append(priority_flag)
        extra["priority"] = 0.85
    confidence, detail, score_flags = score(raw.confidence, located.method, quote, extra=extra)
    params: list[GParam] = []
    seen: set[tuple] = set()
    for rp in raw.parameters:
        gp = ground_param(rp, index, located.start, (located.start, located.end))
        key = gp.value_key() if not gp.unknown else (gp.name, "unknown")
        if key in seen:
            continue
        seen.add(key)
        params.append(gp)
    return GReq(
        statement=statement, category=raw.category, priority=priority, quote=quote, start=located.start,
        end=located.end, line=index.line_of(located.start), section=index.section_of(located.start),
        model_confidence=clamp(raw.confidence), confidence=confidence, detail=detail,
        flags=flags + score_flags, params=params,
    )


def ground_chunk(
    raw: RawExtraction, index: TextIndex, hint: int
) -> tuple[list[GReq], list[GUnspecified], list[dict[str, Any]]]:
    run_flags: list[dict[str, Any]] = []
    reqs = [r for r in (ground_requirement(x, index, hint, run_flags) for x in raw.requirements) if r]
    unspecified = [GUnspecified(u.name, u.reason.strip()) for u in raw.unspecified if NAME_RE.match(u.name)]
    return reqs, unspecified, run_flags


def finalize(
    reqs: list[GReq], unspecified: list[GUnspecified], run_flags: list[dict[str, Any]] | None = None
) -> GroundedExtraction:
    """Merge chunk results: drop duplicates, flag conflicts, turn gaps into explicit unknowns."""
    flags = list(run_flags or [])
    merged: list[GReq] = []
    by_span: dict[tuple[int, int], GReq] = {}
    by_text: dict[str, GReq] = {}
    for req in sorted(reqs, key=lambda r: (r.start, r.end)):
        existing = by_span.get((req.start, req.end)) or by_text.get(normalize(req.statement))
        if existing is None:
            merged.append(req)
            by_span[(req.start, req.end)] = req
            by_text[normalize(req.statement)] = req
            continue
        flags.append({"code": "duplicate_requirement", "statement": req.statement[:120]})
        have = {p.value_key() if not p.unknown else (p.name, "unknown") for p in existing.params}
        for p in req.params:
            key = p.value_key() if not p.unknown else (p.name, "unknown")
            if key not in have:
                existing.params.append(p)
                have.add(key)

    known: dict[str, list[GParam]] = {}
    for req in merged:
        for p in req.params:
            if not p.unknown:
                known.setdefault(p.name, []).append(p)

    for name in SINGLETONS:
        params = known.get(name, [])
        if len({p.value_key() for p in params}) > 1:
            flags.append({"code": "conflicting_values", "name": name})
            for p in params:
                p.flags.append("conflicting_values")
                p.confidence = min(p.confidence, 0.5)
                p.detail["conflict_cap"] = 0.5
                p.detail["final"] = p.confidence

    unknowns: list[GParam] = []
    seen: set[str] = set()
    for item in unspecified:
        if item.name in seen:
            continue
        seen.add(item.name)
        if item.name in known:
            flags.append({"code": "unspecified_but_found", "name": item.name})
            continue
        unknowns.append(_unknown(item.name, "text", item.reason or "Not stated in the document"))

    if not merged and not unknowns:
        flags.append({"code": "no_requirements_found"})
    return GroundedExtraction(merged, unknowns, flags)
