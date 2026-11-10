"""Tool layer. Every tool is read-only on business data, checks its own permission, and returns facts that point at
sources. Figures come from deterministic services (the analysis engine, qualification state, stored records); a tool
never estimates, and what it cannot find it reports as unknown.
"""
import hashlib
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import glossary, retrieval
from app.agent import text as text_utils
from app.agent.sources import Fact, Source, SourceBook
from app.deps import Principal
from app.models import (
    AgentReport,
    AwardRecommendation,
    Comparison,
    Document,
    DocumentExtraction,
    ExtractionRun,
    Requirement,
    Rfq,
    RfqQuote,
    RfqQuoteLine,
    RfqSupplier,
    Supplier,
    SupplierCategory,
    SupplierCategoryLink,
    SupplierContact,
)
from app.rbac import Permission, has_permission
from app.rfqs import comparison as cmp
from app.rfqs import service as rfq_svc
from app.suppliers import service as supplier_svc

logger = logging.getLogger("app.agent")


class ToolError(Exception):
    """Expected failure the user should see (not found, no permission, ambiguous). Recorded in the run."""

    def __init__(self, message: str, *, clarify: bool = False) -> None:
        super().__init__(message)
        self.clarify = clarify


@dataclass
class ToolContext:
    db: AsyncSession
    principal: Principal
    question: str
    book: SourceBook
    focus: dict[str, Any]
    conversation_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None


@dataclass
class ToolResult:
    facts: list[Fact] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    focus: dict[str, str] = field(default_factory=dict)  # entities this answer was about (memory)
    summary: dict[str, Any] = field(default_factory=dict)  # small trace summary
    report_id: str | None = None


# --- Formatting (deterministic) --------------------------------------------------------------------------------------------
def num(value: Any, places: int = 2) -> str:
    if value is None:
        return "unknown"
    d = Decimal(str(value)).quantize(Decimal(1).scaleb(-places))
    text = f"{d:,.{places}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def money(value: Any, currency: str) -> str:
    return "unknown" if value is None else f"{Decimal(str(value)):,.2f} {currency}"


def day(value: Any) -> str:
    if value is None:
        return "not set"
    return value.isoformat()[:10] if hasattr(value, "isoformat") else str(value)[:10]


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def emit(ctx: ToolContext, source: Source, texts: list[str], weight: int = 0) -> list[Fact]:
    """Register a source whose content is exactly these statements, and return them as facts citing it."""
    texts = [t for t in texts if t]
    if not texts:
        return []
    source.content = "\n".join(texts)
    sid = ctx.book.add(source)
    return [Fact(t, [sid], weight) for t in texts]


def require(ctx: ToolContext, *perms: Permission) -> None:
    for perm in perms:
        if not has_permission(ctx.principal.user.role, perm):
            raise ToolError(f"Your role cannot read this information ({perm.value}).")


# --- Entity resolution -------------------------------------------------------------------------------------------------------
async def resolve_rfq(ctx: ToolContext, reference: str | None) -> Rfq:
    org = ctx.principal.org_id
    if not reference:
        focus = ctx.focus.get("rfq_id")
        if focus:
            rows = await retrieval.find_rfqs(ctx.db, org, focus)
            if rows:
                return rows[0]
        numbers = retrieval.RFQ_NUMBER.findall(ctx.question)
        if numbers:
            reference = numbers[0]
        else:
            raise ToolError("Which RFQ do you mean? Give its number, for example RFQ-2026-0001.", clarify=True)
    rows = await retrieval.find_rfqs(ctx.db, org, reference)
    if not rows:
        raise ToolError(f"No RFQ matching '{reference[:80]}' was found in your organization.")
    if len(rows) > 1:
        names = ", ".join(f"{r.number} ({r.title[:40]})" for r in rows[:5])
        raise ToolError(f"Several RFQs match '{reference[:80]}': {names}. Say which one.", clarify=True)
    return rows[0]


async def resolve_supplier(ctx: ToolContext, reference: str | None) -> Supplier:
    org = ctx.principal.org_id
    if not reference:
        mentioned = await retrieval.mentioned_suppliers(ctx.db, org, ctx.question, 1)
        if mentioned:
            return mentioned[0]
        focus = ctx.focus.get("supplier_id")
        if focus:
            rows = await retrieval.find_suppliers(ctx.db, org, focus)
            if rows:
                return rows[0]
        raise ToolError("Which supplier do you mean? Give its name or code.", clarify=True)
    rows = await retrieval.find_suppliers(ctx.db, org, reference)
    exact = [r for r in rows if reference.strip().lower() in (r.name.lower(), r.code.lower())]
    rows = exact or rows
    if not rows:
        raise ToolError(f"No supplier matching '{reference[:80]}' was found in your organization.")
    if len(rows) > 1:
        raise ToolError(f"Several suppliers match '{reference[:80]}': {', '.join(r.name for r in rows[:5])}. Say which one.", clarify=True)
    return rows[0]


async def resolve_document(ctx: ToolContext, reference: str | None) -> Document:
    org = ctx.principal.org_id
    if not reference:
        mentioned = await retrieval.mentioned_documents(ctx.db, org, ctx.question, 1)
        if mentioned:
            return mentioned[0]
        focus = ctx.focus.get("document_id")
        if focus:
            rows = await retrieval.find_documents(ctx.db, org, focus)
            if rows:
                return rows[0]
        raise ToolError("Which document do you mean? Give its file name.", clarify=True)
    rows = await retrieval.find_documents(ctx.db, org, reference)
    if not rows:
        raise ToolError(f"No document matching '{reference[:80]}' was found in your organization.")
    if len(rows) > 1:
        raise ToolError(f"Several documents match '{reference[:80]}': {', '.join(r.filename for r in rows[:5])}. Say which one.", clarify=True)
    return rows[0]


def rfq_source(rfq: Rfq, kind: str = "rfq", suffix: str = "", title: str | None = None) -> Source:
    return Source(key=f"{kind}:{rfq.id}{suffix}", kind=kind, title=title or f"{rfq.number} – {rfq.title}",
                  path=f"/rfqs/{rfq.id}", content="", ref=str(rfq.id), as_of=day(rfq.updated_at))


# --- Tools -------------------------------------------------------------------------------------------------------------------
class SearchArgs(BaseModel):
    query: str = Field(min_length=2, max_length=500)


async def search_knowledge(ctx: ToolContext, args: SearchArgs) -> ToolResult:
    """Full-text search over processed documents, the built-in glossary, and RFQ/supplier names."""
    result = ToolResult()
    org = ctx.principal.org_id
    for entry in glossary.lookup(args.query):
        result.facts += emit(ctx, Source(key=f"glossary:{entry.key}", kind=entry.kind,
                                         title=f"{entry.title} (built-in reference {glossary.GLOSSARY_VERSION})",
                                         path=None, content="", ref=entry.key), [entry.text], 5)
    if has_permission(ctx.principal.user.role, Permission.DOCUMENTS_READ):
        indexed = await retrieval.ensure_indexed(ctx.db, org)
        hits = await retrieval.search_chunks(ctx.db, org, args.query, limit=5)
        result.summary = {"indexed_now": indexed, "hits": len(hits)}
        for h in hits:
            excerpt = retrieval.best_excerpt(h.content, args.query)
            result.facts += emit(ctx, Source(key=f"chunk:{h.chunk_id}", kind="document",
                                             title=f"{h.filename}, passage {h.chunk_no + 1}", path=f"/documents/{h.document_id}",
                                             content="", ref=str(h.document_id)),
                                 [f"{h.filename} (passage {h.chunk_no + 1}) says: “{excerpt}”"], 3)
        if hits:
            result.focus["document_id"] = str(hits[0].document_id)
    if has_permission(ctx.principal.user.role, Permission.RFQS_READ):
        for rfq in await retrieval.find_rfqs(ctx.db, org, args.query, 3) if len(args.query) <= 120 else []:
            result.facts += emit(ctx, rfq_source(rfq), [f"{rfq.number} “{rfq.title}” is an RFQ with status {rfq.status}."], 2)
    if has_permission(ctx.principal.user.role, Permission.SUPPLIERS_READ):
        for s in await retrieval.mentioned_suppliers(ctx.db, org, args.query, 3):
            result.facts += emit(ctx, Source(key=f"supplier:{s.id}", kind="supplier", title=f"{s.name} ({s.code})",
                                             path=f"/suppliers/{s.id}", content="", ref=str(s.id), as_of=day(s.updated_at)),
                                 [f"{s.name} ({s.code}) is a supplier with status {s.status}."], 2)
    if not result.facts:
        result.unknowns.append(f"Nothing in your documents, RFQs, suppliers or the built-in reference matches “{args.query[:120]}”.")
    return result


class RfqArgs(BaseModel):
    rfq: str | None = Field(default=None, max_length=200)


async def rfq_overview(ctx: ToolContext, args: RfqArgs) -> ToolResult:
    require(ctx, Permission.RFQS_READ)
    rfq = await resolve_rfq(ctx, args.rfq)
    db, result = ctx.db, ToolResult(focus={"rfq_id": str(rfq.id)})
    items = await rfq_svc.items_of(db, rfq)
    pairs = await rfq_svc.invited(db, rfq)
    quotes = {q.supplier_id: q for q in await db.scalars(select(RfqQuote).where(RfqQuote.rfq_id == rfq.id))}
    texts = [
        f"{rfq.number} “{rfq.title}” has status {rfq.status.replace('_', ' ')} (version {rfq.version}), currency {rfq.currency}.",
        f"Quotation due date: {day(rfq.response_due_date)}." if rfq.response_due_date else "No quotation due date is set.",
        f"Delivery is required by {day(rfq.required_by)}." if rfq.required_by else "",
        f"Requested Incoterms: {rfq.requested_incoterms}." if rfq.requested_incoterms else "",
        f"Requested payment terms: {rfq.requested_payment_terms}." if rfq.requested_payment_terms else "",
        f"It has {plural(len(items), 'requirement line')} and {plural(len(pairs), 'invited supplier')}.",
    ]
    for n, item in enumerate(items[:25], start=1):
        qty = f"{num(item.quantity)} {item.unit or ''}".strip() if item.quantity is not None else "quantity not stated"
        texts.append(f"Line {n} ({item.priority}): {item.statement[:160]} – {qty}.")
    if rfq.status == "awarded" and rfq.awarded_supplier_id:
        winner = next((s.name for _, s in pairs if s.id == rfq.awarded_supplier_id), None)
        texts.append(f"Awarded to {winner or 'a supplier'} on {day(rfq.awarded_at)}.")
    result.facts += emit(ctx, rfq_source(rfq), texts, 10)

    if pairs:
        line_counts = dict((await db.execute(
            select(RfqQuoteLine.quote_id, func.count()).where(RfqQuoteLine.quote_id.in_([q.id for q in quotes.values()]))
            .group_by(RfqQuoteLine.quote_id))).all()) if quotes else {}
        qtexts = []
        for link, s in pairs:
            q = quotes.get(s.id)
            if q is None:
                qtexts.append(f"{s.name}: invitation {link.status}, no quote recorded.")
            else:
                qtexts.append(
                    f"{s.name}: quote {q.status} ({q.source}), {plural(line_counts.get(q.id, 0), 'line')}, currency "
                    f"{q.currency or 'not stated'}, Incoterms {q.incoterms or 'not stated'}, valid until {day(q.valid_until)}."
                )
        result.facts += emit(ctx, rfq_source(rfq, "quote", ":quotes", f"Quotes on {rfq.number}"), qtexts, 8)
    result.summary = {"rfq": rfq.number, "items": len(items), "suppliers": len(pairs), "quotes": len(quotes)}
    return result


async def _live_analysis(ctx: ToolContext, rfq: Rfq) -> tuple[dict[str, Any], Comparison | None, bool | None]:
    inputs = await rfq_svc.comparison_inputs(ctx.db, rfq)
    result = cmp.compare(inputs, None, **rfq_svc.analysis_params())
    latest = await ctx.db.scalar(select(Comparison).where(Comparison.rfq_id == rfq.id).order_by(Comparison.created_at.desc()).limit(1))
    stale = None
    if latest is not None:
        snapshot = dict(inputs, as_of=latest.result.get("as_of", inputs["as_of"]))
        stale = cmp.inputs_hash(snapshot) != latest.inputs_sha256
    return result, latest, stale


async def compare_suppliers(ctx: ToolContext, args: RfqArgs) -> ToolResult:
    """Run the deterministic analysis engine on the current verified quotes (not stored) and report the ranking."""
    require(ctx, Permission.RFQS_READ, Permission.COMPARISONS_READ)
    rfq = await resolve_rfq(ctx, args.rfq)
    res, latest, stale = await _live_analysis(ctx, rfq)
    result = ToolResult(focus={"rfq_id": str(rfq.id)})
    ccy = res["currency"]
    title = f"Analysis of {rfq.number} computed now (engine {res['engine_version']}, as of {res['as_of']}, inputs {res['inputs_sha256'][:12]})"
    texts: list[str] = []
    if not res["ranking"]:
        texts.append(f"No supplier on {rfq.number} could be scored: no verified, comparable quote.")
    weights = ", ".join(f"{k.replace('_', ' ')} {round(v * 100)}%" for k, v in res["weights"].items())
    texts.append(f"Weights used: {weights} (the defaults).")
    for r in res["ranking"]:
        lead = f"{num(r['lead_time_days'])} days" if r["lead_time_days"] is not None else "lead time not stated"
        risk = r["risk"]
        texts.append(
            f"Rank {r['rank']}: {r['supplier_name']} scores {r['score']:.1f}; basket {money(r['basket_total'], ccy)}; "
            f"{lead}; risk {risk['score']} ({risk['level']}); {'qualified' if r['qualified'] else 'not qualified'}."
        )
        if r.get("moq_excess_total"):
            texts.append(f"{r['supplier_name']}'s minimum order quantities add {money(r['moq_excess_total'], ccy)} to its basket.")
        if r.get("on_time") is False:
            texts.append(f"{r['supplier_name']} would deliver {num(-r['slack_days'])} day(s) after the required date.")
        serious = [d for d in r["deviations"] if d["severity"] in ("critical", "major")]
        for d in serious[:4]:
            texts.append(f"{r['supplier_name']} – {d['severity']} deviation: {d['message']}")
    for e in res["excluded"]:
        texts.append(f"{e['supplier_name']} was not scored: {e['detail']}.")
    texts += res["global_flags"]
    if len(res["ranking"]) > 1:
        texts.append(f"The highest score is {res['ranking'][0]['supplier_name']}. The ranking is decision support; the award needs an approved recommendation.")
    result.facts += emit(ctx, rfq_source(rfq, "analysis", f":{res['inputs_sha256'][:12]}", title), texts, 10)

    if latest is not None:
        state = "outdated (its inputs have changed since)" if stale else "still current"
        result.facts += emit(ctx, Source(key=f"comparison:{latest.id}", kind="comparison",
                                         title=f"Stored comparison of {rfq.number} from {day(latest.created_at)}",
                                         path=f"/rfqs/{rfq.id}", content="", ref=str(latest.id), as_of=day(latest.created_at)),
                             [f"The latest stored comparison of {rfq.number} was run on {day(latest.created_at)} and is {state}."], 4)
    else:
        result.unknowns.append(f"No comparison of {rfq.number} has been stored yet; the figures above were computed now and not saved.")
    result.summary = {"rfq": rfq.number, "scored": len(res["ranking"]), "excluded": len(res["excluded"]),
                      "inputs_sha256": res["inputs_sha256"]}
    return result


class ExplainArgs(BaseModel):
    rfq: str | None = Field(default=None, max_length=200)
    supplier: str | None = Field(default=None, max_length=200)


async def explain_score(ctx: ToolContext, args: ExplainArgs) -> ToolResult:
    """Explain how a supplier's score was reached, criterion by criterion, from the deterministic engine."""
    result = ToolResult()
    method = glossary.by_key("method_scoring")
    assert method is not None
    method_facts = emit(ctx, Source(key="glossary:method_scoring", kind="method",
                                    title=f"{method.title} (built-in reference {glossary.GLOSSARY_VERSION})", path=None,
                                    content="", ref=method.key), [method.text], 1)
    has_rfq = bool(args.rfq or ctx.focus.get("rfq_id") or retrieval.RFQ_NUMBER.search(ctx.question))
    if not has_rfq:
        for entry in [e for e in glossary.lookup(ctx.question, 4) if e.kind == "method" and e.key != "method_scoring"]:
            result.facts += emit(ctx, Source(key=f"glossary:{entry.key}", kind="method",
                                             title=f"{entry.title} (built-in reference {glossary.GLOSSARY_VERSION})",
                                             path=None, content="", ref=entry.key), [entry.text], 2)
        result.facts += method_facts
        return result
    require(ctx, Permission.RFQS_READ, Permission.COMPARISONS_READ)
    rfq = await resolve_rfq(ctx, args.rfq)
    res, _, _ = await _live_analysis(ctx, rfq)
    result.focus["rfq_id"] = str(rfq.id)
    if not res["ranking"]:
        result.unknowns.append(f"No supplier on {rfq.number} has a verified, comparable quote, so there is no score to explain.")
        result.facts += method_facts
        return result
    row = res["ranking"][0]
    if args.supplier or await retrieval.mentioned_suppliers(ctx.db, ctx.principal.org_id, ctx.question, 1):
        supplier = await resolve_supplier(ctx, args.supplier)
        row = next((r for r in res["ranking"] if r["supplier_id"] == str(supplier.id)), None)
        if row is None:
            excluded = next((e for e in res["excluded"] if e["supplier_id"] == str(supplier.id)), None)
            reason = excluded["detail"] if excluded else "it was not invited to this RFQ"
            result.unknowns.append(f"{supplier.name} has no score on {rfq.number}: {reason}.")
            result.facts += method_facts
            return result
        result.focus["supplier_id"] = str(supplier.id)
    ccy = res["currency"]
    title = f"Analysis of {rfq.number} computed now (engine {res['engine_version']}, inputs {res['inputs_sha256'][:12]})"
    texts = [f"{row['supplier_name']} ranks {row['rank']} of {len(res['ranking'])} on {rfq.number} with a score of {row['score']:.1f}."]
    texts += [f"{row['supplier_name']} – {line}" for line in row["explanation"]]
    weights = ", ".join(f"{k.replace('_', ' ')} {round(v * 100)}%" for k, v in res["active_weights"].items())
    texts.append(f"The score is 100 × the weighted sum of the criteria with weights {weights}.")
    for d in row["deviations"]:
        if d["severity"] != "info":
            texts.append(f"{row['supplier_name']} – {d['severity']} deviation: {d['message']}")
    if row["basket_total"] is not None:
        texts.append(f"{row['supplier_name']}'s basket total is {money(row['basket_total'], ccy)}.")
    result.facts += emit(ctx, rfq_source(rfq, "analysis", f":{res['inputs_sha256'][:12]}", title), texts, 10)
    result.facts += method_facts
    result.summary = {"rfq": rfq.number, "supplier": row["supplier_name"], "score": row["score"]}
    return result


class SupplierArgs(BaseModel):
    supplier: str | None = Field(default=None, max_length=200)


async def supplier_profile(ctx: ToolContext, args: SupplierArgs) -> ToolResult:
    require(ctx, Permission.SUPPLIERS_READ)
    s = await resolve_supplier(ctx, args.supplier)
    db = ctx.db
    state = (await supplier_svc.load_state(db, s.organization_id, [s.id]))[s.id].summary(s)
    cats = list(await db.scalars(select(SupplierCategory.name).join(SupplierCategoryLink, SupplierCategoryLink.category_id == SupplierCategory.id)
                                 .where(SupplierCategoryLink.supplier_id == s.id).order_by(SupplierCategory.name)))
    contacts = await db.scalar(select(func.count()).select_from(SupplierContact).where(SupplierContact.supplier_id == s.id)) or 0
    texts = [
        f"{s.name} ({s.code}) has status {s.status.replace('_', ' ')}"
        + (f", country {s.country}" if s.country else "") + ".",
        f"{s.name} is {'currently qualified' if state.qualified else 'not currently qualified'}; qualification score {state.score}.",
        f"{s.name} is missing required checks: {', '.join(state.missing_required)}." if state.missing_required else "",
        f"{s.name} has expired evidence for: {', '.join(state.expired)}." if state.expired else "",
        f"{s.name}'s categories: {', '.join(cats)}." if cats else f"{s.name} has no categories assigned.",
        f"{s.name} has {plural(contacts, 'contact')}.",
        f"{s.name}'s typical lead time is {s.typical_lead_time_days} days." if s.typical_lead_time_days is not None else "",
        f"{s.name}'s usual payment terms: {s.payment_terms}." if s.payment_terms else "",
        f"{s.name}'s usual currency: {s.currency}." if s.currency else "",
    ]
    if has_permission(ctx.principal.user.role, Permission.RFQS_READ):
        rows = (await db.execute(
            select(Rfq.number, Rfq.status, Rfq.awarded_supplier_id, RfqQuote.status)
            .join(RfqSupplier, RfqSupplier.rfq_id == Rfq.id)
            .outerjoin(RfqQuote, (RfqQuote.rfq_id == Rfq.id) & (RfqQuote.supplier_id == s.id))
            .where(RfqSupplier.supplier_id == s.id).order_by(Rfq.created_at.desc()).limit(10)
        )).all()
        if rows:
            texts.append(f"{s.name} was invited to {plural(len(rows), 'RFQ')} (latest first): "
                         + "; ".join(f"{n} ({st}, quote {qs or 'none'}{', awarded' if aw == s.id else ''})" for n, st, aw, qs in rows) + ".")
        else:
            texts.append(f"{s.name} has not been invited to any RFQ yet.")
    result = ToolResult(focus={"supplier_id": str(s.id)})
    result.facts += emit(ctx, Source(key=f"supplier:{s.id}", kind="supplier", title=f"{s.name} ({s.code})",
                                     path=f"/suppliers/{s.id}", content="", ref=str(s.id), as_of=day(s.updated_at)), texts, 10)
    result.summary = {"supplier": s.code, "qualified": state.qualified}
    return result


class DocumentArgs(BaseModel):
    document: str | None = Field(default=None, max_length=300)


async def document_summary(ctx: ToolContext, args: DocumentArgs) -> ToolResult:
    require(ctx, Permission.DOCUMENTS_READ)
    doc = await resolve_document(ctx, args.document)
    db, result = ctx.db, ToolResult(focus={"document_id": str(doc.id)})
    pages = doc.file_metadata.get("pages") if isinstance(doc.file_metadata, dict) else None
    texts = [
        f"{doc.filename} ({doc.content_type}, {num(doc.size_bytes / 1024, 0)} KB"
        + (f", {pages} pages" if pages else "") + f") has status {doc.status}; uploaded {day(doc.created_at)}.",
    ]
    if doc.status == "quarantined":
        texts.append(f"{doc.filename} was quarantined by the malware scan and its content is not available.")
    src = Source(key=f"document:{doc.id}", kind="document", title=doc.filename, path=f"/documents/{doc.id}",
                 content="", ref=str(doc.id), as_of=day(doc.updated_at))
    result.facts += emit(ctx, src, texts, 10)

    extraction = await db.scalar(select(DocumentExtraction).where(DocumentExtraction.document_id == doc.id)
                                 .order_by(DocumentExtraction.created_at.desc()).limit(1))
    if extraction is not None:
        data = extraction.result or {}
        etexts = []
        if data.get("document_type"):
            etexts.append(f"The document analysis classified {doc.filename} as {str(data['document_type']).replace('_', ' ')}.")
        if data.get("summary"):
            etexts.append(f"The automated document analysis (model-written when the document was processed) summarized "
                          f"{doc.filename} as: {str(data['summary'])[:600]}")
        for key, label in (("supplier_name", "supplier"), ("currency", "currency"), ("incoterms", "Incoterms"),
                           ("payment_terms", "payment terms"), ("valid_until", "validity")):
            fld = data.get(key)
            if isinstance(fld, dict) and not fld.get("unknown") and fld.get("value") is not None:
                quote = f" (quoted: “{str(fld.get('source'))[:120]}”)" if fld.get("source") else ""
                etexts.append(f"{doc.filename} states the {label} as {fld['value']}{quote}.")
        items = data.get("line_items") or []
        if items:
            etexts.append(f"The analysis found {plural(len(items), 'line item')} in {doc.filename}.")
        if extraction.needs_review:
            etexts.append(f"The analysis of {doc.filename} was flagged for human review.")
        result.facts += emit(ctx, Source(key=f"docanalysis:{extraction.id}", kind="document",
                                         title=f"Document analysis of {doc.filename} ({extraction.provider}, prompt {extraction.prompt_version}; fields checked against the text)",
                                         path=f"/documents/{doc.id}", content="", ref=str(doc.id),
                                         as_of=day(extraction.created_at)), etexts, 8)
    else:
        result.unknowns.append(f"{doc.filename} has no AI document analysis ({doc.analysis_status}).")

    if doc.extracted_text and doc.scan_status == "clean":
        quotes = text_utils.key_sentences(doc.extracted_text)
        if quotes:
            result.facts += emit(ctx, Source(key=f"doctext:{doc.id}", kind="document",
                                             title=f"{doc.filename}, key passages (verbatim)", path=f"/documents/{doc.id}",
                                             content="", ref=str(doc.id)), [f"{doc.filename} says: “{q}”" for q in quotes], 6)
    elif doc.status != "quarantined":
        result.unknowns.append(f"No text could be read from {doc.filename}.")

    runs = (await db.execute(select(ExtractionRun.id, ExtractionRun.status, func.count(Requirement.id))
                             .outerjoin(Requirement, Requirement.run_id == ExtractionRun.id)
                             .where(ExtractionRun.document_id == doc.id).group_by(ExtractionRun.id, ExtractionRun.status,
                                                                                    ExtractionRun.created_at)
                             .order_by(ExtractionRun.created_at.desc()).limit(3))).all()
    if runs and has_permission(ctx.principal.user.role, Permission.REQUIREMENTS_READ):
        run_id, status, count = runs[0]
        result.facts += emit(ctx, Source(key=f"requirements:{run_id}", kind="requirements",
                                         title=f"Requirement extraction for {doc.filename}", path=f"/requirements/{run_id}",
                                         content="", ref=str(run_id)),
                             [f"The latest requirement extraction for {doc.filename} has status {status.replace('_', ' ')} "
                              f"with {plural(count, 'requirement')}."], 5)
    result.summary = {"document": doc.filename, "analysis": bool(extraction)}
    return result


async def next_actions(ctx: ToolContext, args: RfqArgs) -> ToolResult:
    """Deterministic next steps from workflow state, for one RFQ or across the organization."""
    if args.rfq or ctx.focus.get("rfq_id") or retrieval.RFQ_NUMBER.search(ctx.question):
        return await _rfq_actions(ctx, args)
    return await _org_actions(ctx)


async def _rfq_actions(ctx: ToolContext, args: RfqArgs) -> ToolResult:
    require(ctx, Permission.RFQS_READ)
    rfq = await resolve_rfq(ctx, args.rfq)
    db, result = ctx.db, ToolResult(focus={"rfq_id": str(rfq.id)})
    today = datetime.now(UTC).date()
    acts: list[str] = []
    if rfq.status == "draft":
        problems = await rfq_svc.readiness(db, rfq)
        acts += [f"Before submitting {rfq.number}: {p}" for p in problems]
        if not problems:
            acts.append(f"{rfq.number} is complete; submit it for approval.")
    elif rfq.status == "pending_approval":
        acts.append(f"{rfq.number} is waiting for approval by someone other than its creator and submitter.")
    elif rfq.status == "approved":
        acts.append(f"{rfq.number} is approved; generate the email packages and send it.")
    elif rfq.status in ("sent", "closed"):
        pairs = await rfq_svc.invited(db, rfq)
        quotes = {q.supplier_id: q for q in await db.scalars(select(RfqQuote).where(RfqQuote.rfq_id == rfq.id))}
        missing = [s.name for _, s in pairs if s.id not in quotes]
        if missing:
            overdue = rfq.response_due_date is not None and rfq.response_due_date < today
            acts.append(f"No quote yet from {', '.join(missing)}" + (f"; the due date {day(rfq.response_due_date)} has passed, follow up." if overdue else "."))
        drafts = [s.name for _, s in pairs if s.id in quotes and quotes[s.id].status == "draft"]
        if drafts:
            acts.append(f"Check and verify the draft quote(s) from {', '.join(drafts)}; only verified quotes are compared.")
        if quotes:
            suggested = await db.scalar(select(func.count()).select_from(RfqQuoteLine).where(
                RfqQuoteLine.quote_id.in_([q.id for q in quotes.values()]), RfqQuoteLine.match_status == "suggested")) or 0
            if suggested:
                acts.append(f"Confirm or clear {plural(suggested, 'suggested line match', 'suggested line matches')} on {rfq.number}.")
            soon = [s.name for _, s in pairs if s.id in quotes and quotes[s.id].valid_until is not None
                    and today <= quotes[s.id].valid_until <= date.fromordinal(today.toordinal() + 7)]
            if soon:
                acts.append(f"Quotes from {', '.join(soon)} expire within 7 days; decide or ask for an extension.")
            res, latest, stale = await _live_analysis(ctx, rfq)
            no_rate = [e["supplier_name"] for e in res["excluded"] if e["reason"] == "no_fx_rate"]
            if no_rate:
                acts.append(f"Record an exchange rate under Rates & units so the quote(s) from {', '.join(no_rate)} can be compared.")
            if latest is None and res["ranking"]:
                acts.append(f"Run and store a comparison of {rfq.number} so a recommendation can be made.")
            elif latest is not None and stale:
                acts.append(f"The stored comparison of {rfq.number} is outdated; run it again.")
            pending = await db.scalar(select(AwardRecommendation).where(AwardRecommendation.rfq_id == rfq.id,
                                                                         AwardRecommendation.status == "pending"))
            if pending is not None:
                acts.append(f"A recommendation for {rfq.number} is waiting for a decision by an approver who did not make it.")
            elif latest is not None and not stale and res["ranking"]:
                acts.append(f"Recommend a supplier for {rfq.number} from the current comparison, with a rationale.")
        if not acts:
            acts.append(f"{rfq.number} has no open actions.")
    elif rfq.status == "awarded":
        acts.append(f"{rfq.number} was awarded on {day(rfq.awarded_at)}; no further sourcing action is open.")
    else:
        acts.append(f"{rfq.number} is cancelled.")
    result.facts += emit(ctx, rfq_source(rfq, "rfq", ":actions", f"Workflow state of {rfq.number} on {today.isoformat()}"), acts, 9)
    result.summary = {"rfq": rfq.number, "actions": len(acts)}
    return result


async def _org_actions(ctx: ToolContext) -> ToolResult:
    db, org, role = ctx.db, ctx.principal.org_id, ctx.principal.user.role
    result = ToolResult()
    today = datetime.now(UTC).date()

    async def count(stmt) -> int:
        return int(await db.scalar(stmt) or 0)

    checks: list[tuple[Permission, str, str, Any]] = [
        (Permission.RFQS_READ, "/rfqs", "RFQs waiting for approval",
         select(func.count()).select_from(Rfq).where(Rfq.organization_id == org, Rfq.status == "pending_approval")),
        (Permission.RFQS_READ, "/rfqs", "RFQs past their quotation due date and still open",
         select(func.count()).select_from(Rfq).where(Rfq.organization_id == org, Rfq.status == "sent", Rfq.response_due_date < today)),
        (Permission.RFQS_READ, "/rfqs", "draft quotes waiting to be verified",
         select(func.count()).select_from(RfqQuote).join(Rfq, Rfq.id == RfqQuote.rfq_id)
         .where(RfqQuote.organization_id == org, RfqQuote.status == "draft", Rfq.status.in_(("sent", "closed")))),
        (Permission.RFQS_READ, "/rfqs", "award recommendations waiting for a decision",
         select(func.count()).select_from(AwardRecommendation).where(AwardRecommendation.organization_id == org,
                                                                    AwardRecommendation.status == "pending")),
        (Permission.REQUIREMENTS_READ, "/review", "requirement extractions waiting for review or approval",
         select(func.count()).select_from(ExtractionRun).where(ExtractionRun.organization_id == org,
                                                              ExtractionRun.status.in_(("pending_review", "ready_for_approval")))),
        (Permission.SUPPLIERS_READ, "/suppliers", "suppliers waiting for a qualification decision",
         select(func.count()).select_from(Supplier).where(Supplier.organization_id == org, Supplier.status == "under_review")),
        (Permission.DOCUMENTS_READ, "/documents", "documents that failed processing",
         select(func.count()).select_from(Document).where(Document.organization_id == org, Document.status == "failed")),
    ]
    texts = []
    for perm, path, label, stmt in checks:
        if not has_permission(role, perm):
            continue
        n = await count(stmt)
        if n:
            texts.append(f"There {'is' if n == 1 else 'are'} {n} {label} (see {path}).")
    if not texts:
        texts.append("No sourcing work is waiting in the queues you can see.")
    result.facts += emit(ctx, Source(key=f"queue:{today.isoformat()}", kind="queue", title=f"Work queues on {today.isoformat()}",
                                     path="/dashboard", content=""), texts, 9)
    result.summary = {"queues": len(texts)}
    return result


class GlossaryArgs(BaseModel):
    term: str = Field(min_length=2, max_length=200)


async def lookup_term(ctx: ToolContext, args: GlossaryArgs) -> ToolResult:
    result = ToolResult()
    entries = list({e.key: e for e in glossary.lookup(ctx.question) + glossary.lookup(args.term)}.values())[:4]
    for entry in entries:
        result.facts += emit(ctx, Source(key=f"glossary:{entry.key}", kind=entry.kind,
                                         title=f"{entry.title} (built-in reference {glossary.GLOSSARY_VERSION})", path=None,
                                         content="", ref=entry.key), [entry.text], 5)
    if not entries:
        result.unknowns.append(f"The built-in reference has no entry for “{args.term[:80]}”.")
    return result


class ReportArgs(BaseModel):
    kind: Literal["rfq_status", "supplier_comparison", "supplier_profile"]
    rfq: str | None = Field(default=None, max_length=200)
    supplier: str | None = Field(default=None, max_length=200)


async def generate_report(ctx: ToolContext, args: ReportArgs) -> ToolResult:
    """Build a stored, cited report from other tools' facts. Writes only the report record."""
    sections: list[tuple[str, ToolResult]] = []
    if args.kind == "supplier_profile":
        prof = await supplier_profile(ctx, SupplierArgs(supplier=args.supplier))
        sections.append(("Supplier profile", prof))
        subject = prof.focus.get("supplier_id")
        title_entity = (await ctx.db.get(Supplier, uuid.UUID(subject))) if subject else None
        title = f"Supplier profile – {title_entity.name if title_entity else 'supplier'}"
    else:
        overview = await rfq_overview(ctx, RfqArgs(rfq=args.rfq))
        rfq = await ctx.db.get(Rfq, uuid.UUID(overview.focus["rfq_id"]))
        assert rfq is not None
        ref = RfqArgs(rfq=str(rfq.id))
        sections.append(("Request", overview))
        if args.kind == "supplier_comparison" or rfq.status in ("sent", "closed", "awarded"):
            if has_permission(ctx.principal.user.role, Permission.COMPARISONS_READ):
                sections.append(("Supplier comparison", await compare_suppliers(ctx, ref)))
        sections.append(("Next actions", await next_actions(ctx, ref)))
        subject = str(rfq.id)
        title = f"{'Supplier comparison' if args.kind == 'supplier_comparison' else 'RFQ status'} – {rfq.number}"
    blocks: list[dict[str, Any]] = []
    unknowns: list[str] = []
    for heading, part in sections:
        blocks.append({"type": "heading", "text": heading, "sources": []})
        blocks += [{"type": "text", "text": f.text, "sources": f.sources} for f in sorted(part.facts, key=lambda f: -f.weight)]
        unknowns += part.unknowns
    used = sorted({s for b in blocks for s in b["sources"]}, key=lambda x: int(x[1:]))
    sources = ctx.book.used(set(used))
    digest = hashlib.sha256(json.dumps({"blocks": blocks, "sources": sources}, sort_keys=True).encode()).hexdigest()
    report = AgentReport(organization_id=ctx.principal.org_id, conversation_id=ctx.conversation_id, run_id=ctx.run_id,
                         kind=args.kind, title=title, subject_id=uuid.UUID(subject) if subject else None, blocks=blocks,
                         unknowns=unknowns, sources=sources, content_sha256=digest, created_by=ctx.principal.user.id)
    ctx.db.add(report)
    await ctx.db.flush()
    result = ToolResult(unknowns=unknowns, report_id=str(report.id))
    for _, part in sections:
        result.focus.update(part.focus)
    result.facts += emit(ctx, Source(key=f"report:{report.id}", kind="report", title=title,
                                     path=f"/assistant/reports/{report.id}", content="", ref=str(report.id),
                                     as_of=datetime.now(UTC).date().isoformat()),
                         [f"Report “{title}” was created with {plural(len([b for b in blocks if b['type'] == 'text']), 'cited statement')} "
                          f"from {plural(len(sources), 'source')}; open it to read or download it as PDF or Markdown."], 20)
    # the report's own statements are part of this answer too
    for _, part in sections:
        result.facts += part.facts
    result.summary = {"report_id": str(report.id), "kind": args.kind, "statements": len(blocks)}
    return result


# --- Registry ----------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args: type[BaseModel]
    run: Callable[[ToolContext, Any], Awaitable[ToolResult]]


TOOLS: dict[str, Tool] = {t.name: t for t in (
    Tool("search_knowledge", "Search uploaded documents (full text), the built-in sourcing reference and RFQ/supplier names. "
         "Use for open questions or when no specific entity is named.", SearchArgs, search_knowledge),
    Tool("rfq_overview", "Status, requirement lines, dates, requested terms and quote status of one RFQ. rfq = number "
         "(RFQ-2026-0001), title words, or empty for the RFQ discussed earlier.", RfqArgs, rfq_overview),
    Tool("compare_suppliers", "Deterministic analysis of the verified quotes on an RFQ: ranking, basket totals, lead times, "
         "MOQ, risk and deviations, suppliers not scored.", RfqArgs, compare_suppliers),
    Tool("explain_score", "Criterion-by-criterion explanation of a supplier's score on an RFQ (top-ranked when no supplier "
         "is given), or the scoring method when no RFQ is given.", ExplainArgs, explain_score),
    Tool("supplier_profile", "A supplier's status, qualification, categories, terms and RFQ history.", SupplierArgs, supplier_profile),
    Tool("document_summary", "Summary of one uploaded document: metadata, grounded analysis fields, verbatim key passages, "
         "requirement extraction status.", DocumentArgs, document_summary),
    Tool("next_actions", "Open next steps for one RFQ, or across the organization's work queues when no RFQ is given.",
         RfqArgs, next_actions),
    Tool("lookup_term", "Definition of a sourcing term (Incoterms, MOQ, payment terms, ...) or of how this application "
         "scores, converts and awards.", GlossaryArgs, lookup_term),
    Tool("generate_report", "Create a stored, cited report: rfq_status or supplier_comparison (needs rfq) or "
         "supplier_profile (needs supplier).", ReportArgs, generate_report),
)}


def parse_args(name: str, raw: dict[str, Any] | None) -> BaseModel:
    tool = TOOLS[name]
    try:
        return tool.args.model_validate({k: v for k, v in (raw or {}).items() if v not in ("", None)})
    except ValidationError as exc:
        raise ToolError(f"Invalid arguments for {name}: {exc.errors()[0]['msg']}") from None


async def execute(ctx: ToolContext, name: str, args: BaseModel) -> tuple[ToolResult | None, dict[str, Any]]:
    """Run one tool; returns its result and the trace record (errors are captured, never raised)."""
    started = time.monotonic()
    trace: dict[str, Any] = {"tool": name, "args": args.model_dump(exclude_none=True)}
    try:
        async with ctx.db.begin_nested():
            result = await TOOLS[name].run(ctx, args)
        trace.update(status="ok", facts=len(result.facts), unknowns=len(result.unknowns),
                     sources=sorted({s for f in result.facts for s in f.sources}, key=lambda x: int(x[1:])),
                     summary=result.summary)
        return result, trace
    except ToolError as exc:
        trace.update(status="clarify" if exc.clarify else "error", error=str(exc))
        return ToolResult(unknowns=[str(exc)]), trace
    except Exception as exc:  # a tool bug must not take the answer down; it is recorded and reported as unknown
        logger.exception("agent tool %s failed", name)
        trace.update(status="error", error=f"{type(exc).__name__}", exception=True)
        return ToolResult(unknowns=[f"The {name.replace('_', ' ')} step failed, so that information is unknown in this answer."]), trace
    finally:
        trace["ms"] = int((time.monotonic() - started) * 1000)


def describe_tools() -> list[dict[str, Any]]:
    return [{"name": t.name, "description": t.description, "args": t.args.model_json_schema()} for t in TOOLS.values()]
