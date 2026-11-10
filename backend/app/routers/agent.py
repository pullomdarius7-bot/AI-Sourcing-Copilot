"""Agent API: conversations, questions, feedback, run traces, reports, status."""
import re
import uuid
from urllib.parse import quote as urlquote

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import framework, glossary, memory, prompts, reports, retrieval, tools
from app.agent import schemas as ag
from app.audit import record
from app.db import get_db, set_tenant
from app.deps import Principal, require_permission
from app.errors import not_found
from app.models import (
    AgentConversation,
    AgentMessage,
    AgentReport,
    AgentRun,
    Document,
    KnowledgeChunk,
    Organization,
    User,
)
from app.rbac import Permission, has_permission

router = APIRouter(prefix="/agent", tags=["agent"])
USE, AUDIT = Permission.AGENT_USE, Permission.AGENT_AUDIT


def _brief(run: AgentRun | None) -> ag.RunBrief | None:
    if run is None:
        return None
    return ag.RunBrief(id=run.id, mode=run.mode, status=run.status, intent=run.intent, provider=run.provider, model=run.model,
                       prompt_version=run.prompt_version, latency_ms=run.latency_ms,
                       tools=[{k: c.get(k) for k in ("tool", "status", "ms", "error")} for c in run.tool_calls],
                       flags=list(run.safety.get("flags", [])), model_output_rejected=run.safety.get("model_output_rejected"))


def _message(m: AgentMessage, runs: dict[uuid.UUID, AgentRun]) -> ag.MessageOut:
    return ag.MessageOut(id=m.id, role=m.role, content=m.content, blocks=m.blocks, unknowns=m.unknowns, follow_ups=m.follow_ups,
                         sources=m.sources, run=_brief(runs.get(m.run_id)) if m.run_id else None, feedback=m.feedback,
                         created_at=m.created_at)


def _conversation(c: AgentConversation) -> ag.ConversationOut:
    return ag.ConversationOut(id=c.id, title=c.title, archived=c.archived, context=c.context, created_at=c.created_at,
                              updated_at=c.updated_at)


async def _own(db: AsyncSession, p: Principal, conversation_id: uuid.UUID, *, lock: bool = False) -> AgentConversation:
    stmt = select(AgentConversation).where(AgentConversation.id == conversation_id, AgentConversation.organization_id == p.org_id,
                                           AgentConversation.user_id == p.user.id)
    conv = await db.scalar(stmt.with_for_update() if lock else stmt)
    if conv is None:
        raise not_found("Conversation not found")
    return conv


async def _runs_for(db: AsyncSession, messages: list[AgentMessage]) -> dict[uuid.UUID, AgentRun]:
    ids = [m.run_id for m in messages if m.run_id]
    return {r.id: r for r in await db.scalars(select(AgentRun).where(AgentRun.id.in_(ids)))} if ids else {}


@router.get("/status", response_model=ag.StatusOut)
async def status(p: Principal = Depends(require_permission(USE)), db: AsyncSession = Depends(get_db)):
    mode, providers, _ = framework.choose_mode()
    indexed = await db.scalar(select(func.count(func.distinct(KnowledgeChunk.document_id))).where(KnowledgeChunk.organization_id == p.org_id))
    has_chunks = select(KnowledgeChunk.id).where(KnowledgeChunk.document_id == Document.id).exists()
    pending = await db.scalar(select(func.count()).select_from(Document).where(
        Document.organization_id == p.org_id, Document.status == "processed", Document.scan_status == "clean",
        Document.extracted_text.is_not(None), ~has_chunks))
    return ag.StatusOut(mode=mode, configured_mode=framework.settings.agent_mode, providers=[x.name for x in providers],
                        prompt_version=prompts.PROMPT_VERSION, prompt_sha256=prompts.prompt_sha256(),
                        glossary_version=glossary.GLOSSARY_VERSION,
                        tools=[{"name": t.name, "description": t.description} for t in tools.TOOLS.values()],
                        indexed_documents=indexed or 0, unindexed_documents=pending or 0)


@router.get("/conversations", response_model=list[ag.ConversationOut])
async def list_conversations(archived: bool = False, p: Principal = Depends(require_permission(USE)), db: AsyncSession = Depends(get_db)):
    rows = await db.scalars(select(AgentConversation).where(
        AgentConversation.organization_id == p.org_id, AgentConversation.user_id == p.user.id,
        AgentConversation.archived == archived).order_by(AgentConversation.updated_at.desc()).limit(200))
    return [_conversation(c) for c in rows]


@router.post("/conversations", response_model=ag.ConversationOut, status_code=201)
async def create_conversation(body: ag.ConversationIn, p: Principal = Depends(require_permission(USE)), db: AsyncSession = Depends(get_db)):
    conv = AgentConversation(organization_id=p.org_id, user_id=p.user.id, title=(body.title or "New conversation").strip()[:200])
    db.add(conv)
    await db.flush()
    await db.refresh(conv)
    await db.commit()
    return _conversation(conv)


@router.get("/conversations/{conversation_id}", response_model=ag.ConversationDetail)
async def get_conversation(conversation_id: uuid.UUID, p: Principal = Depends(require_permission(USE)), db: AsyncSession = Depends(get_db)):
    conv = await _own(db, p, conversation_id)
    messages = list(await db.scalars(select(AgentMessage).where(AgentMessage.conversation_id == conv.id).order_by(AgentMessage.created_at, AgentMessage.role.desc())))
    runs = await _runs_for(db, messages)
    return ag.ConversationDetail(**_conversation(conv).model_dump(), messages=[_message(m, runs) for m in messages])


@router.patch("/conversations/{conversation_id}", response_model=ag.ConversationOut)
async def update_conversation(conversation_id: uuid.UUID, body: ag.ConversationPatch, p: Principal = Depends(require_permission(USE)),
                              db: AsyncSession = Depends(get_db)):
    conv = await _own(db, p, conversation_id, lock=True)
    if body.title is not None:
        conv.title = body.title.strip()
    if body.archived is not None:
        conv.archived = body.archived
    await db.commit()
    return _conversation(conv)


async def _turn(db: AsyncSession, p: Principal, conv: AgentConversation, question: str) -> ag.TurnOut:
    if conv.title == "New conversation":
        conv.title = memory.title_from(question)
    turn = await framework.ask(db, p, conv, question)
    await set_tenant(db, p.org_id)
    runs = {turn.run.id: turn.run}
    return ag.TurnOut(conversation=_conversation(conv), user=_message(turn.user, runs), assistant=_message(turn.assistant, runs))


@router.post("/conversations/{conversation_id}/messages", response_model=ag.TurnOut, status_code=201)
async def ask(conversation_id: uuid.UUID, body: ag.AskIn, p: Principal = Depends(require_permission(USE)), db: AsyncSession = Depends(get_db)):
    conv = await _own(db, p, conversation_id, lock=True)
    if conv.archived:
        conv.archived = False
    return await _turn(db, p, conv, body.question)


@router.post("/ask", response_model=ag.TurnOut, status_code=201)
async def ask_new(body: ag.StartIn, p: Principal = Depends(require_permission(USE)), db: AsyncSession = Depends(get_db)):
    """Ask in an existing conversation, or start one."""
    if body.conversation_id:
        conv = await _own(db, p, body.conversation_id, lock=True)
    else:
        conv = AgentConversation(organization_id=p.org_id, user_id=p.user.id, title=memory.title_from(body.question))
        db.add(conv)
        await db.flush()
    return await _turn(db, p, conv, body.question)


@router.post("/messages/{message_id}/feedback", response_model=ag.MessageOut)
async def feedback(message_id: uuid.UUID, body: ag.FeedbackIn, p: Principal = Depends(require_permission(USE)), db: AsyncSession = Depends(get_db)):
    msg = await db.scalar(select(AgentMessage).join(AgentConversation, AgentConversation.id == AgentMessage.conversation_id).where(
        AgentMessage.id == message_id, AgentMessage.organization_id == p.org_id, AgentConversation.user_id == p.user.id,
        AgentMessage.role == "assistant"))
    if msg is None:
        raise not_found("Message not found")
    msg.feedback, msg.feedback_note = body.rating, (body.note or None)
    record(db, org_id=p.org_id, user_id=p.user.id, action="agent.feedback", ip=p.ip,
           meta={"message_id": str(msg.id), "run_id": str(msg.run_id), "rating": body.rating})
    await db.commit()
    runs = await _runs_for(db, [msg])
    return _message(msg, runs)


async def _run_out(db: AsyncSession, run: AgentRun) -> ag.RunOut:
    email = await db.scalar(select(User.email).where(User.id == run.user_id))
    answer = await db.scalar(select(AgentMessage).where(AgentMessage.run_id == run.id))
    return ag.RunOut(id=run.id, conversation_id=run.conversation_id, user_id=run.user_id, user_email=email, question=run.question,
                     mode=run.mode, status=run.status, intent=run.intent, prompt_version=run.prompt_version,
                     prompt_sha256=run.prompt_sha256, provider=run.provider, model=run.model, plan=run.plan,
                     tool_calls=run.tool_calls, llm_calls=run.llm_calls, safety=run.safety, source_ids=run.source_ids,
                     answer_sha256=run.answer_sha256, latency_ms=run.latency_ms, input_tokens=run.input_tokens,
                     output_tokens=run.output_tokens, error=run.error, created_at=run.created_at,
                     answer=_message(answer, {run.id: run}) if answer else None)


@router.get("/runs", response_model=ag.RunListOut)
async def list_runs(status: str | None = Query(default=None, pattern="^(answered|fallback|refused|clarify|error)$"),
                    mode: str | None = Query(default=None, pattern="^(deterministic|llm)$"), user_id: uuid.UUID | None = None,
                    limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
                    p: Principal = Depends(require_permission(AUDIT)), db: AsyncSession = Depends(get_db)):
    stmt = select(AgentRun).where(AgentRun.organization_id == p.org_id)
    if status:
        stmt = stmt.where(AgentRun.status == status)
    if mode:
        stmt = stmt.where(AgentRun.mode == mode)
    if user_id:
        stmt = stmt.where(AgentRun.user_id == user_id)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(await db.scalars(stmt.order_by(AgentRun.created_at.desc()).limit(limit).offset(offset)))
    return ag.RunListOut(items=[await _run_out(db, r) for r in rows], total=total)


@router.get("/runs/{run_id}", response_model=ag.RunOut)
async def get_run(run_id: uuid.UUID, p: Principal = Depends(require_permission(USE)), db: AsyncSession = Depends(get_db)):
    run = await db.scalar(select(AgentRun).where(AgentRun.id == run_id, AgentRun.organization_id == p.org_id))
    if run is None or (run.user_id != p.user.id and not has_permission(p.user.role, AUDIT)):
        raise not_found("Run not found")
    return await _run_out(db, run)


def _report_out(r: AgentReport) -> ag.ReportOut:
    return ag.ReportOut(id=r.id, kind=r.kind, title=r.title, subject_id=r.subject_id, run_id=r.run_id, conversation_id=r.conversation_id,
                        blocks=r.blocks, unknowns=r.unknowns, sources=r.sources, content_sha256=r.content_sha256,
                        created_by=r.created_by, created_at=r.created_at)


async def _report(db: AsyncSession, p: Principal, report_id: uuid.UUID) -> AgentReport:
    r = await db.scalar(select(AgentReport).where(AgentReport.id == report_id, AgentReport.organization_id == p.org_id))
    if r is None or (r.created_by != p.user.id and not has_permission(p.user.role, AUDIT)):
        raise not_found("Report not found")
    return r


@router.get("/reports", response_model=list[ag.ReportOut])
async def list_reports(p: Principal = Depends(require_permission(USE)), db: AsyncSession = Depends(get_db)):
    stmt = select(AgentReport).where(AgentReport.organization_id == p.org_id)
    if not has_permission(p.user.role, AUDIT):
        stmt = stmt.where(AgentReport.created_by == p.user.id)
    return [_report_out(r) for r in await db.scalars(stmt.order_by(AgentReport.created_at.desc()).limit(100))]


@router.get("/reports/{report_id}", response_model=ag.ReportOut)
async def get_report(report_id: uuid.UUID, p: Principal = Depends(require_permission(USE)), db: AsyncSession = Depends(get_db)):
    return _report_out(await _report(db, p, report_id))


def _export_dict(r: AgentReport) -> dict:
    return {"title": r.title, "created_at": r.created_at.strftime("%Y-%m-%d %H:%M UTC"), "blocks": r.blocks, "unknowns": r.unknowns,
            "sources": r.sources, "content_sha256": r.content_sha256}


def _download(data: bytes, media_type: str, filename: str) -> Response:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", filename)[:120]
    return Response(data, media_type=media_type, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{urlquote(safe)}", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox"})


@router.get("/reports/{report_id}/pdf")
async def report_pdf(report_id: uuid.UUID, p: Principal = Depends(require_permission(USE)), db: AsyncSession = Depends(get_db)):
    r = await _report(db, p, report_id)
    org = await db.scalar(select(Organization.name).where(Organization.id == p.org_id)) or "Organization"
    data = reports.to_pdf(_export_dict(r), org)
    record(db, org_id=p.org_id, user_id=p.user.id, action="agent.report_exported", ip=p.ip, meta={"report_id": str(r.id), "format": "pdf"})
    await db.commit()
    return _download(data, "application/pdf", f"{r.title}.pdf")


@router.get("/reports/{report_id}/markdown")
async def report_markdown(report_id: uuid.UUID, p: Principal = Depends(require_permission(USE)), db: AsyncSession = Depends(get_db)):
    r = await _report(db, p, report_id)
    data = reports.to_markdown(_export_dict(r)).encode()
    record(db, org_id=p.org_id, user_id=p.user.id, action="agent.report_exported", ip=p.ip, meta={"report_id": str(r.id), "format": "md"})
    await db.commit()
    return _download(data, "text/markdown; charset=utf-8", f"{r.title}.md")


@router.post("/reindex")
async def reindex(p: Principal = Depends(require_permission(Permission.ORG_MANAGE)), db: AsyncSession = Depends(get_db)):
    """Rebuild the search index for every processed document in the organization."""
    docs = list(await db.scalars(select(Document).where(Document.organization_id == p.org_id, Document.status == "processed")))
    passages = 0
    for doc in docs:
        passages += await retrieval.index_document(db, doc)
    record(db, org_id=p.org_id, user_id=p.user.id, action="agent.reindexed", ip=p.ip, meta={"documents": len(docs), "passages": passages})
    await db.commit()
    return {"documents": len(docs), "passages": passages}


