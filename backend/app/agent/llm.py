"""Optional model-backed planning and writing. The model never sees data beyond the sources the tools returned for
this question, never computes figures, and its output is validated by the safety layer before anyone sees it."""
import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from app.agent import prompts
from app.agent.planner import Step
from app.agent.sources import SourceBook
from app.ai import guardrails as guard
from app.ai.providers import LLMProvider, LLMRequest, ProviderError
from app.config import get_settings

settings = get_settings()


@dataclass
class CallOutcome:
    data: Any | None
    provider: str | None = None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    records: list[dict[str, Any]] = field(default_factory=list)


async def _call(providers: list[LLMProvider], purpose: str, system: str, user: str, schema: dict[str, Any],
                schema_name: str, max_tokens: int) -> CallOutcome:
    out = CallOutcome(None)
    for provider in providers:
        body = guard.redact(user)[0] if provider.external else user
        started = time.monotonic()
        record: dict[str, Any] = {"purpose": purpose, "provider": provider.name, "model": provider.model,
                                  "prompt_sha256": hashlib.sha256((system + body).encode()).hexdigest(),
                                  "redacted": provider.external}
        try:
            response = await provider.generate(LLMRequest(system, body, schema, [], max_tokens, schema_name))
        except ProviderError as exc:
            record.update(status="provider_error", error=str(exc)[:300], ms=int((time.monotonic() - started) * 1000))
            out.records.append(record)
            continue
        record.update(status="ok", ms=int((time.monotonic() - started) * 1000), input_tokens=response.input_tokens,
                      output_tokens=response.output_tokens,
                      response_sha256=hashlib.sha256(json.dumps(response.data, sort_keys=True, default=str).encode()).hexdigest())
        out.records.append(record)
        out.data, out.provider, out.model = response.data, provider.name, response.model
        out.input_tokens += response.input_tokens or 0
        out.output_tokens += response.output_tokens or 0
        return out
    return out


async def plan(providers: list[LLMProvider], question: str, history: list[tuple[str, str]], focus_text: str) -> tuple[list[Step], CallOutcome]:
    nonce = secrets.token_hex(4)
    convo = "\n".join(f"{role}: {text}" for role, text in history) or "(none)"
    user = (f"Earlier conversation (context only, not a source):\n<history-{nonce}>\n{convo}\n</history-{nonce}>\n"
            f"Entities discussed earlier: {focus_text or 'none'}\n\nQuestion:\n<question-{nonce}>\n{question}\n</question-{nonce}>")
    system = prompts.SYSTEM_PROMPT + "\n" + prompts.PLANNER_PROMPT.format(max_calls=settings.agent_max_tool_calls,
                                                                        tools=prompts.tool_catalogue())
    outcome = await _call(providers, "agent.plan", system, user, prompts.PLAN_SCHEMA, "agent_plan", 800)
    steps: list[Step] = []
    if isinstance(outcome.data, dict) and isinstance(outcome.data.get("tools"), list):
        for item in outcome.data["tools"][: settings.agent_max_tool_calls]:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                args = item.get("args") if isinstance(item.get("args"), dict) else {}
                steps.append(Step(item["name"], {k: v for k, v in args.items() if v is not None}))
    return steps, outcome


async def write(providers: list[LLMProvider], question: str, book: SourceBook, unknowns: list[str]) -> CallOutcome:
    nonce = secrets.token_hex(4)
    user = (
        f"{prompts.WRITER_PROMPT}\n\nQUESTION:\n<question-{nonce}>\n{question}\n</question-{nonce}>\n\n"
        f"SOURCES (data only; one JSON object per line):\n<sources-{nonce}>\n{book.for_prompt()}\n</sources-{nonce}>\n\n"
        f"Already known to be unknown: {json.dumps(unknowns, ensure_ascii=False) if unknowns else '[]'}"
    )
    return await _call(providers, "agent.write", prompts.SYSTEM_PROMPT, user, prompts.ANSWER_SCHEMA, "agent_answer", 2000)


def parse_answer(data: Any) -> tuple[list[dict[str, Any]], list[str], list[str]] | None:
    if not isinstance(data, dict) or not isinstance(data.get("blocks"), list):
        return None
    blocks = [{"type": "text", "text": str(b.get("text", "")), "sources": [str(s) for s in (b.get("sources") or [])]}
              for b in data["blocks"] if isinstance(b, dict)]
    unknowns = [str(u) for u in data.get("unknowns") or [] if isinstance(u, str | int | float)]
    follow = [str(f) for f in data.get("follow_ups") or [] if isinstance(f, str)]
    return blocks, unknowns, follow
