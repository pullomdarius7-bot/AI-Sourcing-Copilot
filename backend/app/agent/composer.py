"""Deterministic answer writer: turns tool facts into cited blocks. No wording is invented beyond fixed headings."""
from typing import Any

from app.agent.planner import FOLLOW_UPS
from app.agent.sources import Fact

HEADINGS = {
    "rfq_overview": "RFQ",
    "compare_suppliers": "Supplier comparison",
    "explain_score": "Score explanation",
    "supplier_profile": "Supplier",
    "document_summary": "Document",
    "next_actions": "Next actions",
    "search_knowledge": "What I found",
    "lookup_term": "Reference",
    "generate_report": "Report",
}
MAX_BLOCKS = 40


def compose(intent: str, results: list[tuple[str, list[Fact]]], unknowns: list[str]) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = []
    seen: set[str] = set()
    multi = len([r for r in results if r[1]]) > 1
    for tool, facts in results:
        ordered = [f for _, f in sorted(enumerate(facts), key=lambda t: (-t[1].weight, t[0]))]
        fresh: list[Fact] = []
        for f in ordered:
            if f.text not in seen:
                seen.add(f.text)
                fresh.append(f)
        if not fresh:
            continue
        if multi:
            blocks.append({"type": "heading", "text": HEADINGS.get(tool, tool.replace("_", " ").capitalize()), "sources": []})
        for f in fresh:
            blocks.append({"type": "text", "text": f.text, "sources": list(f.sources)})
    if len(blocks) > MAX_BLOCKS:
        blocks = blocks[:MAX_BLOCKS]
        unknowns = [*unknowns, "The answer was shortened; open the cited pages for the full detail."]
    return {"blocks": blocks, "unknowns": list(dict.fromkeys(unknowns)), "follow_ups": FOLLOW_UPS.get(intent, [])[:3]}


def to_markdown(blocks: list[dict[str, Any]], unknowns: list[str], notice: str | None = None) -> str:
    lines: list[str] = []
    if notice:
        lines.append(notice)
    for b in blocks:
        if b["type"] == "heading":
            lines.append(f"\n### {b['text']}")
        else:
            cites = "".join(f"[{s}]" for s in b["sources"])
            lines.append(f"- {b['text']} {cites}")
    if unknowns:
        lines.append("\n**Unknown**")
        lines += [f"- {u}" for u in unknowns]
    return "\n".join(lines).strip()
