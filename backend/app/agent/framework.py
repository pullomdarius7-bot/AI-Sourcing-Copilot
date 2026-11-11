"""Agent framework: plan -> act -> compose -> verify -> persist.

Two modes share every layer except the planner and writer:
  deterministic - rule-based planner and writer. Always available, no model needed; the default when none is set up.
  llm           - a configured model plans (validated against the tool registry) and writes (validated by the safety
                  layer). Rejected output falls back to the deterministic answer, and the rejection is recorded.
"""
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import audit, composer, llm, memory, prompts, retrieval, safety, tools
from app.agent import planner as rules
from app.agent.sources import Fact, SourceBook
from app.ai.service import get_ai_service
from app.config import get_settings
from app.db import set_tenant
from app.deps import Principal
from app.models import AgentConversation, AgentMessage, AgentRun

settings = get_settings()
logger = logging.getLogger("app.agent")
CLARIFY_NOTICE = "I need a little more detail to answer that."
NOTHING_FOUND = "I could not find information about this in the data you can access, so the answer is unknown."


@dataclass
class Turn:
    user: AgentMessage
    assistant: AgentMessage
    run: AgentRun


def choose_mode() -> tuple[str, list, list[str]]:
    flags: list[str] = []
    wanted = settings.agent_mode
    if wanted == "deterministic":
        return "deterministic", [], flags
    providers = get_ai_service().providers
    if providers:
        return "llm", providers, flags
    if wanted == "llm":
        flags.append("llm_mode_without_provider")
    return "deterministic", [], flags


async def ask(db: AsyncSession, p: Principal, conversation: AgentConversation, question: str) -> Turn:
    audit.check_rate(p)
    question = question.strip()[: settings.agent_max_question_chars]
    run_id = uuid.uuid4()
    started = time.monotonic()
    conversation_id = conversation.id
    user_msg = AgentMessage(organization_id=p.org_id, conversation_id=conversation_id, role="user", content=question)
    db.add(user_msg)
    await db.flush()
    try:
        async with db.begin_nested():
            run, answer = await _answer(db, p, conversation, question, run_id, started)
    except Exception as exc:  # the user gets an honest failure, and the failure is still traceable
        logger.exception("agent run failed")
        await set_tenant(db, p.org_id)
        await db.refresh(conversation)  # anything the failed savepoint touched is reloaded, not trusted
        run = AgentRun(id=run_id, organization_id=p.org_id, conversation_id=conversation_id, user_id=p.user.id,
                       question=question, mode="deterministic", status="error", prompt_version=prompts.PROMPT_VERSION,
                       prompt_sha256=prompts.prompt_sha256(), error=type(exc).__name__,
                       latency_ms=int((time.monotonic() - started) * 1000))
        answer = {"content": "Something went wrong while answering, so nothing was answered. The failure has been recorded.",
                  "blocks": [], "unknowns": [], "follow_ups": [], "sources": []}
        db.add(run)
        await db.flush()
    assistant = AgentMessage(organization_id=p.org_id, conversation_id=conversation_id, role="assistant",
                             content=answer["content"], blocks=answer["blocks"], unknowns=answer["unknowns"],
                             follow_ups=answer["follow_ups"], sources=answer["sources"], run_id=run.id)
    db.add(assistant)
    conversation.updated_at = datetime.now(UTC)
    audit.log_run(db, p, run)
    await db.flush()
    await db.commit()
    return Turn(user_msg, assistant, run)


async def _answer(db: AsyncSession, p: Principal, conversation: AgentConversation, question: str, run_id: uuid.UUID,
                  started: float) -> tuple[AgentRun, dict[str, Any]]:
    verdict = safety.check_question(question, settings.agent_max_question_chars)
    mode, providers, mode_flags = choose_mode()
    safety_record: dict[str, Any] = {"flags": verdict.flags + mode_flags, "problems": []}
    run = AgentRun(id=run_id, organization_id=p.org_id, conversation_id=conversation.id, user_id=p.user.id,
                   question=question, mode=mode, status="answered", prompt_version=prompts.PROMPT_VERSION,
                   prompt_sha256=prompts.prompt_sha256())
    llm_calls: list[dict[str, Any]] = []

    if verdict.refuse:
        run.status, run.intent = "refused", "action_request"
        return _finish(db, run, safety_record, llm_calls, [], [], {"content": verdict.refuse, "blocks": [], "unknowns": [],
                                                                   "follow_ups": ["What are the next actions on this RFQ?"],
                                                                   "sources": []}, started)

    focus = dict(conversation.context or {})
    supplier_mentioned = bool(await retrieval.mentioned_suppliers(db, p.org_id, question, 1))
    document_mentioned = bool(await retrieval.mentioned_documents(db, p.org_id, question, 1))
    fallback_plan = rules.plan(question, focus, supplier_mentioned=supplier_mentioned, document_mentioned=document_mentioned)
    intent, steps, plan_source = fallback_plan.intent, fallback_plan.steps, "rules"

    if mode == "llm":
        convo = await memory.history(db, conversation, settings.agent_history_turns)
        model_steps, outcome = await llm.plan(providers, question, convo, await memory.describe_focus(db, focus))
        llm_calls += outcome.records
        valid = [s for s in model_steps if s.tool in tools.TOOLS and _args_ok(s)]
        if model_steps and len(valid) < len(model_steps):
            safety_record["flags"].append("model_plan_steps_rejected")
        if valid:
            steps, plan_source = valid, "model"
            intent = _intent_of(valid, fallback_plan.intent)
        elif outcome.data is not None and isinstance(outcome.data, dict) and outcome.data.get("tools") == []:
            safety_record["flags"].append("model_planned_no_tools")
        _usage(run, outcome)

    book = SourceBook()
    ctx = tools.ToolContext(db=db, principal=p, question=question, book=book, focus=focus,
                            conversation_id=conversation.id, run_id=run_id)
    traces: list[dict[str, Any]] = []
    results: list[tuple[str, list[Fact]]] = []
    unknowns: list[str] = []
    new_focus: dict[str, str] = {}
    report_ids: list[str] = []
    for step in steps[: settings.agent_max_tool_calls]:
        try:
            args = tools.parse_args(step.tool, step.args)
        except tools.ToolError as exc:
            traces.append({"tool": step.tool, "args": step.args, "status": "invalid_args", "error": str(exc), "ms": 0})
            continue
        result, trace = await tools.execute(ctx, step.tool, args)
        traces.append(trace)
        if result is None:
            continue
        results.append((step.tool, result.facts))
        unknowns += result.unknowns
        new_focus.update(result.focus)
        if result.report_id:
            report_ids.append(result.report_id)
        ctx.focus = {**ctx.focus, **result.focus}
    run.plan = [{"tool": s.tool, "args": s.args, "by": plan_source} for s in steps[: settings.agent_max_tool_calls]]
    run.intent = intent
    safety_record["flags"] += safety.source_flags(book)

    draft = composer.compose(intent, results, unknowns)
    checked = safety.validate(draft["blocks"], draft["unknowns"], draft["follow_ups"], book, question)
    if checked.problems:  # the deterministic writer only restates facts; a problem here is a defect worth recording
        safety_record["problems"] += [f"deterministic: {x}" for x in checked.problems]
    final = checked

    if mode == "llm" and any(f for _, f in results):
        outcome = await llm.write(providers, question, book, checked.unknowns)
        llm_calls += outcome.records
        _usage(run, outcome)
        parsed = llm.parse_answer(outcome.data) if outcome.data is not None else None
        if parsed is None:
            safety_record["model_output_rejected"] = "no valid answer from any provider"
            run.status = "fallback"
        else:
            m_blocks, m_unknowns, m_follow = parsed
            model_checked = safety.validate(m_blocks, m_unknowns + checked.unknowns, m_follow, book, question)
            if model_checked.problems or not model_checked.blocks:
                safety_record["model_output_rejected"] = "; ".join(model_checked.problems or ["no grounded statement"])[:1000]
                run.status = "fallback"
            else:
                final = safety.Checked(model_checked.blocks, list(dict.fromkeys(model_checked.unknowns)),
                                       model_checked.follow_ups or checked.follow_ups, [])

    clarify = [t for t in traces if t.get("status") == "clarify"]
    notice = None
    if not final.blocks:
        if clarify:
            run.status, notice = "clarify", CLARIFY_NOTICE
            asks = safety.clarifications([t.get("error", "") for t in clarify])
            final.unknowns[:] = list(dict.fromkeys(asks + final.unknowns))
        elif any(t.get("exception") for t in traces):
            run.status = "error"
        if not final.unknowns:
            final.unknowns.append(NOTHING_FOUND)
    used = {s for b in final.blocks for s in b["sources"]}
    sources = book.used(used)
    content = composer.to_markdown(final.blocks, final.unknowns, notice)
    answer = {"content": content, "blocks": final.blocks, "unknowns": final.unknowns, "follow_ups": final.follow_ups,
              "sources": sources}
    memory.remember(conversation, new_focus, intent)
    if report_ids:
        safety_record["reports"] = report_ids  # each report also carries this run's id
    return _finish(db, run, safety_record, llm_calls, traces, sorted(used, key=lambda x: int(x[1:])), answer, started)


def _args_ok(step: rules.Step) -> bool:
    try:
        tools.parse_args(step.tool, step.args)
    except tools.ToolError:
        return False
    return True


def _intent_of(steps: list[rules.Step], default: str) -> str:
    mapping = {"compare_suppliers": "compare", "explain_score": "explain", "rfq_overview": "rfq", "supplier_profile": "supplier",
               "document_summary": "summarize", "next_actions": "next_actions", "lookup_term": "define",
               "search_knowledge": "search", "generate_report": "report"}
    return mapping.get(steps[0].tool, default) if steps else default


def _usage(run: AgentRun, outcome: llm.CallOutcome) -> None:
    if outcome.provider:
        run.provider, run.model = outcome.provider, outcome.model
    if outcome.input_tokens or outcome.output_tokens:
        run.input_tokens = (run.input_tokens or 0) + outcome.input_tokens
        run.output_tokens = (run.output_tokens or 0) + outcome.output_tokens


def _finish(db: AsyncSession, run: AgentRun, safety_record: dict[str, Any], llm_calls: list[dict[str, Any]],
            traces: list[dict[str, Any]], used: list[str], answer: dict[str, Any], started: float) -> tuple[AgentRun, dict[str, Any]]:
    run.safety = safety_record
    run.llm_calls = llm_calls
    run.tool_calls = traces
    run.source_ids = used
    run.answer_sha256 = hashlib.sha256(json.dumps(
        {"blocks": answer["blocks"], "unknowns": answer["unknowns"], "sources": answer["sources"]}, sort_keys=True
    ).encode()).hexdigest()
    run.latency_ms = int((time.monotonic() - started) * 1000)
    db.add(run)
    return run, answer
