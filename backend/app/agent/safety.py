"""Safety layer.

Input: length limits, refusal of requests to act (the agent is read-only), prompt-injection signals.
Output: every statement must cite known sources, and every number in it must appear in what it cites. Anything that
fails is not shown; the run falls back to the deterministic answer and the failure is recorded.
"""
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from app.agent.sources import SourceBook

_ACTION = re.compile(
    r"^\s*(?:(?:please|pls|kindly|now|then|ok|okay)\s*,?\s+)*(?:(?:can|could|would|will)\s+you\s+|go ahead and\s+|i want you to\s+)?"
    r"(award|approve|reject|send|resend|e-?mail|delete|remove|cancel|submit|verify|qualify|disqualify|block|unblock|"
    r"withdraw|reopen|close|edit|change|update|modify|set|invite|add|record|upload|create|place)\b"
    r"(?!\s+(?:me|us)\b)(?!\s+(?:a\s+|an\s+|the\s+|me\s+a\s+)?(?:report|summary|write-?up))",
    re.IGNORECASE,
)
_INJECTION = re.compile(
    r"ignore (?:all |any |the )?(?:previous|prior|above|earlier) (?:instructions|rules)|disregard (?:the|your) (?:rules|instructions)|"
    r"system prompt|you are now|developer mode|act as (?:an? )?(?:admin|system)|reveal (?:your|the) (?:prompt|instructions)|"
    r"<\s*/?\s*(?:system|instructions?)\s*>",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\w])|(?<![\w.,])\d+(?:\.\d+)?")
_REFERENCE = re.compile(r"\b(?:lines?|rows?|pages?|passages?|steps?|items?)\s+\d+(?:\s*(?:,|and|-)\s*\d+)*", re.IGNORECASE)
_CITATION = re.compile(r"\[\s*S\d+\s*\]")
_MARKUP = re.compile(r"<[^>]{0,200}>|javascript:|https?://", re.IGNORECASE)
MAX_BLOCK_CHARS = 1200


@dataclass
class InputVerdict:
    refuse: str | None = None
    flags: list[str] = field(default_factory=list)


def check_question(question: str, max_chars: int) -> InputVerdict:
    verdict = InputVerdict()
    if _INJECTION.search(question):
        verdict.flags.append("prompt_injection_pattern_in_question")
    match = _ACTION.match(question)
    if match:
        verdict.refuse = (
            f"I can't {match.group(1).lower()} anything: the assistant only reads and explains your sourcing data. "
            "Use the RFQ, supplier or quote pages to make changes; those actions are permission-checked and recorded."
        )
        verdict.flags.append(f"action_request:{match.group(1).lower()}")
    if len(question) > max_chars:
        verdict.flags.append("question_truncated")
    return verdict


def source_flags(book: SourceBook) -> list[str]:
    """Sources that contain instruction-like text. They stay usable as data; the flag is recorded on the run."""
    return [f"instruction_like_text_in_{s.id}" for s in book.sources if _INJECTION.search(s.content)]


def numbers_in(text: str) -> set[Decimal]:
    out = set()
    for raw in _NUMBER.findall(_CITATION.sub(" ", text)):
        try:
            out.add(Decimal(raw.replace(",", "")).normalize())
        except InvalidOperation:
            continue
    return out


def clean_text(text: str) -> str:
    text = _CITATION.sub("", text)
    return re.sub(r"\s+([.,;:!?])", r"\1", " ".join(text.split()))


@dataclass
class Checked:
    blocks: list[dict[str, Any]]
    unknowns: list[str]
    follow_ups: list[str]
    problems: list[str]


def validate(blocks: list[dict[str, Any]], unknowns: list[str], follow_ups: list[str], book: SourceBook,
             question: str) -> Checked:
    """Keep only grounded statements. A problem in any block is reported; callers decide whether to fall back."""
    known = book.ids()
    question_numbers = numbers_in(question)
    all_numbers = set().union(*(numbers_in(s.content) for s in book.sources)) if book.sources else set()
    kept: list[dict[str, Any]] = []
    problems: list[str] = []
    for n, raw in enumerate(blocks, start=1):
        kind = raw.get("type", "text")
        text = clean_text(str(raw.get("text", "")))
        sources = [s for s in dict.fromkeys(str(x).strip() for x in raw.get("sources") or [])]
        if not text:
            continue
        if len(text) > MAX_BLOCK_CHARS:
            problems.append(f"block {n}: too long")
            continue
        if _MARKUP.search(text):
            problems.append(f"block {n}: markup or external link")
            continue
        if kind == "heading":
            stray = numbers_in(text) - all_numbers - question_numbers
            if stray:
                problems.append(f"block {n}: heading has numbers not in any source ({_fmt(stray)})")
                continue
            kept.append({"type": "heading", "text": text, "sources": []})
            continue
        if not sources:
            problems.append(f"block {n}: no source cited")
            continue
        unknown_ids = [s for s in sources if s not in known]
        if unknown_ids:
            problems.append(f"block {n}: cites unknown source(s) {', '.join(unknown_ids)}")
            continue
        cited = set().union(*(numbers_in(book.get(s).content) for s in sources))  # type: ignore[union-attr]
        stray = numbers_in(text) - cited - question_numbers
        if stray:
            problems.append(f"block {n}: number(s) {_fmt(stray)} not in the cited sources")
            continue
        kept.append({"type": "text", "text": text, "sources": sources})
    clean_unknowns = []
    for u in unknowns[:10]:
        text = clean_text(str(u))[:300]
        if not text:
            continue
        stray = numbers_in(_REFERENCE.sub(" ", text)) - all_numbers - question_numbers
        if stray or _MARKUP.search(text):
            problems.append(f"unknown statement with ungrounded content ({_fmt(stray) or 'markup'})")
            continue
        clean_unknowns.append(text)
    clean_follow = [clean_text(str(f))[:200] for f in follow_ups[:3] if clean_text(str(f)) and not _MARKUP.search(str(f))]
    return Checked(kept, clean_unknowns, clean_follow, problems)


def _fmt(values: set[Decimal]) -> str:
    return ", ".join(sorted(format(v, "f") for v in values))[:120]
