"""Audit layer: per-user rate limiting, the organization audit log entry, and metrics for every run."""
import time
from collections import defaultdict, deque

from sqlalchemy.ext.asyncio import AsyncSession

from app import metrics
from app.audit import record
from app.config import get_settings
from app.deps import Principal
from app.errors import ApiError
from app.models import AgentRun

settings = get_settings()
_hits: dict[str, deque[float]] = defaultdict(deque)


def reset() -> None:
    _hits.clear()


def check_rate(p: Principal) -> None:
    if not settings.rate_limit_enabled:
        return
    key = str(p.user.id)
    now = time.monotonic()
    bucket = _hits[key]
    while bucket and now - bucket[0] > 60:
        bucket.popleft()
    if len(bucket) >= settings.agent_rate_per_minute:
        raise ApiError(429, "rate_limited", "Too many questions in a minute. Try again shortly.",
                       {"Retry-After": str(max(1, int(60 - (now - bucket[0]))))})
    bucket.append(now)


def log_run(db: AsyncSession, p: Principal, run: AgentRun) -> None:
    record(db, org_id=p.org_id, user_id=p.user.id, action="agent.answered", ip=p.ip, meta={
        "run_id": str(run.id), "conversation_id": str(run.conversation_id), "mode": run.mode, "status": run.status,
        "intent": run.intent, "tools": [c.get("tool") for c in run.tool_calls], "sources": len(run.source_ids),
        "provider": run.provider,
    })
    metrics.AGENT_ANSWERS.inc(mode=run.mode, status=run.status)
    metrics.AGENT_DURATION.observe(run.latency_ms / 1000, mode=run.mode)
    for call in run.tool_calls:
        metrics.AGENT_TOOL_CALLS.inc(tool=call.get("tool", ""), status=call.get("status", ""))
    for flag in run.safety.get("flags", []):
        metrics.AGENT_SAFETY.inc(event=flag.split(":")[0])
    if run.safety.get("model_output_rejected"):
        metrics.AGENT_SAFETY.inc(event="model_output_rejected")
    if run.status == "refused":
        metrics.AGENT_SAFETY.inc(event="refused")
    for call in run.llm_calls:
        if call.get("status") == "ok":
            metrics.LLM_TOKENS.inc(call.get("input_tokens") or 0, provider=call["provider"], direction="input")
            metrics.LLM_TOKENS.inc(call.get("output_tokens") or 0, provider=call["provider"], direction="output")
