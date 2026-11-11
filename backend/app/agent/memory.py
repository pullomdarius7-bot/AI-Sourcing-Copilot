"""Memory layer.

Short-term: the last turns of the conversation, given to the model planner as context (never as a source).
Working memory: the entities the conversation is about (RFQ, supplier, document), so "this RFQ" resolves.
Long-term: every message and run is stored; nothing is learned across users or organizations.
"""
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentConversation, AgentMessage, Document, Rfq, Supplier

FOCUS_KEYS = ("rfq_id", "supplier_id", "document_id")


async def history(db: AsyncSession, conversation: AgentConversation, turns: int) -> list[tuple[str, str]]:
    rows = list(await db.scalars(
        select(AgentMessage).where(AgentMessage.conversation_id == conversation.id)
        .order_by(AgentMessage.created_at.desc()).limit(turns * 2)
    ))
    return [(m.role, m.content[:800]) for m in reversed(rows)]


def remember(conversation: AgentConversation, focus: dict[str, str], intent: str) -> None:
    context = dict(conversation.context or {})
    for key in FOCUS_KEYS:
        if focus.get(key):
            context[key] = focus[key]
    context["last_intent"] = intent
    conversation.context = context


async def describe_focus(db: AsyncSession, context: dict[str, Any]) -> str:
    parts = []
    if context.get("rfq_id"):
        rfq = await db.scalar(select(Rfq).where(Rfq.id == context["rfq_id"]))
        if rfq:
            parts.append(f"RFQ {rfq.number} ({rfq.title[:60]})")
    if context.get("supplier_id"):
        s = await db.scalar(select(Supplier).where(Supplier.id == context["supplier_id"]))
        if s:
            parts.append(f"supplier {s.name}")
    if context.get("document_id"):
        d = await db.scalar(select(Document).where(Document.id == context["document_id"]))
        if d:
            parts.append(f"document {d.filename}")
    return ", ".join(parts)


def title_from(question: str) -> str:
    text = re.sub(r"\s+", " ", question).strip()
    return (text[:77] + "…") if len(text) > 80 else text or "New conversation"
