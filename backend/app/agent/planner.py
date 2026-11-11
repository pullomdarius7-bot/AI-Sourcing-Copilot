"""Deterministic planner: maps a question to tool calls with ordered rules. Always available, and the fallback when a
model's plan is missing or invalid. Same question and conversation focus, same plan."""
import re
from dataclasses import dataclass, field
from typing import Any

from app.agent import glossary
from app.agent.text import RFQ_NUMBER


@dataclass
class Step:
    tool: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class Plan:
    intent: str
    steps: list[Step]


def _has(pattern: str, text: str) -> bool:
    return re.search(pattern, text, re.IGNORECASE) is not None


REPORT = r"\b(report|write-?up|briefing)\b"
NEXT = (r"\bnext (step|action)s?\b|\bwhat should (i|we) do\b|\bto-?dos?\b|\bwhat('s| is) (pending|open|waiting)\b|"
        r"\bopen (items|tasks|actions)\b|\bprioriti[sz]e\b|\bwhat needs\b|\bblock(ed|ers?)\b|\bstuck\b")
EXPLAIN = (r"\b(explain|why|how come|breakdown|break down)\b.*\b(score|scored|rank|ranked|ranking|first|top|last|lower|higher)\b|"
           r"\bhow (was|is|were|are) .*\b(score|scored|rank|ranked|calculated)\b|\bscore breakdown\b")
COMPARE = (r"\bwho should (we|i) (award|choose|pick|select|go with)\b|\brecommend(ation|ed)?\b.*\b(supplier|award|rfq)\b|"
           r"\bcompar(e|ison|ing)\b|\b(best|cheapest|lowest|fastest|quickest|highest[- ]scor\w*|winning|recommended)\b|"
           r"\brank(ing|ed)?\b|\bwhich (supplier|quote|offer|vendor)\b|\bwho (is|has|offers|gives)\b.*\b(best|cheapest|lowest|fastest)\b|"
           r"\b(risks?|deviations?|moq|minimum order|lead times?)\b.*\b(suppliers?|quotes?|offers?)\b")
SUMMARY = r"\b(summari[sz]e|summary|tl;?dr|key points|main points|what does .* (say|contain)|gist)\b"
DOCUMENT = r"\b(document|file|pdf|attachment|spreadsheet|sheet|contract|quote document|datasheet|uploaded)\b"
SUPPLIER_Q = (r"\b(tell me about|profile|who is|qualified|qualification|status of|details (of|on|about)|history|"
              r"background|information on|info on)\b")
RFQ_Q = (r"\b(status|overview|details|lines?|items?|due|deadline|when|quotes?|invited|suppliers? on|requested|"
         r"incoterms|currency|tell me about|what is happening|where are we)\b")
DEFINE = r"\b(what (is|are|does|do)|define|definition|meaning|mean|explain|difference between|how does)\b"


def plan(question: str, focus: dict[str, Any], *, supplier_mentioned: bool = False, document_mentioned: bool = False) -> Plan:
    q = question.strip()
    numbers = RFQ_NUMBER.findall(q)
    rfq = numbers[0].upper() if numbers else None
    has_rfq = bool(rfq or focus.get("rfq_id"))
    rfq_ref_in_text = bool(rfq) or _has(r"\b(this|that|the|same) (rfq|request|tender)\b|\bit\b", q)

    if _has(REPORT, q):
        if _has(r"\bcompar", q) and has_rfq:
            return Plan("report", [Step("generate_report", {"kind": "supplier_comparison", "rfq": rfq})])
        if supplier_mentioned and not rfq:
            return Plan("report", [Step("generate_report", {"kind": "supplier_profile"})])
        if has_rfq:
            return Plan("report", [Step("generate_report", {"kind": "rfq_status", "rfq": rfq})])
        return Plan("report", [Step("generate_report", {"kind": "rfq_status", "rfq": None})])

    if _has(NEXT, q):
        return Plan("next_actions", [Step("next_actions", {"rfq": rfq} if (rfq or rfq_ref_in_text) else {})])

    if _has(EXPLAIN, q):
        return Plan("explain", [Step("explain_score", {"rfq": rfq})] if has_rfq else [Step("explain_score", {})])

    if _has(SUMMARY, q) and (document_mentioned or _has(DOCUMENT, q) or focus.get("document_id")):
        return Plan("summarize", [Step("document_summary", {})])

    terms = glossary.lookup(q)
    if terms and _has(DEFINE, q) and not rfq and not supplier_mentioned:
        return Plan("define", [Step("lookup_term", {"term": terms[0].terms[0]})])

    if _has(COMPARE, q) and (has_rfq or not supplier_mentioned):
        return Plan("compare", [Step("compare_suppliers", {"rfq": rfq})])

    if supplier_mentioned and (_has(SUPPLIER_Q, q) or not rfq):
        return Plan("supplier", [Step("supplier_profile", {})])

    if rfq or (focus.get("rfq_id") and _has(RFQ_Q, q) and rfq_ref_in_text):
        steps = [Step("rfq_overview", {"rfq": rfq})]
        if supplier_mentioned:
            steps.append(Step("supplier_profile", {}))
        return Plan("rfq", steps)

    if document_mentioned:
        return Plan("summarize", [Step("document_summary", {})])

    return Plan("search", [Step("search_knowledge", {"query": q[:500]})])


FOLLOW_UPS: dict[str, list[str]] = {
    "compare": ["Explain the top supplier's score", "What are the next actions on this RFQ?", "Generate a supplier comparison report"],
    "explain": ["Compare all suppliers on this RFQ", "How is risk scored?", "What are the next actions on this RFQ?"],
    "rfq": ["Compare the suppliers on this RFQ", "What are the next actions on this RFQ?", "Generate an RFQ status report"],
    "next_actions": ["Compare the suppliers on this RFQ", "Generate an RFQ status report"],
    "supplier": ["Generate a supplier profile report", "What should we do next?"],
    "summarize": ["What does it say about payment terms?", "What should we do next?"],
    "define": ["How does this application score quotes?", "What should we do next?"],
    "search": ["What should we do next?", "How does this application score quotes?"],
    "report": ["What are the next actions on this RFQ?"],
}
