"""Suggest which RFQ line a quoted line answers. Suggestions only: a person confirms every match."""
import re
from typing import Any

STOP = {
    "the", "and", "for", "with", "of", "to", "in", "on", "per", "pcs", "pc", "piece", "pieces", "unit", "units",
    "a", "an", "or", "be", "is", "are", "shall", "must", "should", "will", "supplier", "deliver", "provide",
    "price", "qty", "quantity", "item", "total", "each",
}
THRESHOLD = 0.34


def tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+(?:[.,][0-9]+)?", text.lower())
    return {w for w in words if w not in STOP and (len(w) > 1 or w.isdigit())}


def similarity(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    overlap = len(ta & tb)
    return round(overlap / min(len(ta), len(tb)) * 0.6 + overlap / len(ta | tb) * 0.4, 3)


def item_text(item: dict[str, Any]) -> str:
    params = " ".join(
        f"{p.get('name', '')} {p.get('value', '')} {p.get('unit') or ''}"
        for p in item.get("parameters") or [] if not p.get("unknown")
    )
    return f"{item['statement']} {params}"


def suggest(lines: list[dict[str, Any]], items: list[dict[str, Any]]) -> list[tuple[str | None, float]]:
    """Greedy one-to-one assignment by descending similarity. Returns (item id or None, score) per line."""
    scored = [
        (similarity(line["description"], item_text(item)), li, item["id"])
        for li, line in enumerate(lines) for item in items
    ]
    scored.sort(key=lambda x: (-x[0], x[1]))
    taken_lines: set[int] = set()
    taken_items: set[str] = set()
    result: list[tuple[str | None, float]] = [(None, 0.0)] * len(lines)
    for score, li, item_id in scored:
        if score < THRESHOLD:
            break
        if li in taken_lines or item_id in taken_items:
            continue
        result[li] = (item_id, score)
        taken_lines.add(li)
        taken_items.add(item_id)
    return result
