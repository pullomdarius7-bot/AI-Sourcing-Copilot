import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=4000)

    @field_validator("question")
    @classmethod
    def not_blank(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Ask a question of at least two characters")
        return value


class StartIn(AskIn):
    conversation_id: uuid.UUID | None = None


class ConversationIn(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ConversationPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    archived: bool | None = None


class FeedbackIn(BaseModel):
    rating: Literal[-1, 1]
    note: str | None = Field(default=None, max_length=1000)


class RunBrief(BaseModel):
    id: uuid.UUID
    mode: str
    status: str
    intent: str | None
    provider: str | None
    model: str | None
    prompt_version: str
    latency_ms: int
    tools: list[dict[str, Any]]
    flags: list[str]
    model_output_rejected: str | None


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    blocks: list[dict[str, Any]]
    unknowns: list[str]
    follow_ups: list[str]
    sources: list[dict[str, Any]]
    run: RunBrief | None
    feedback: int | None
    created_at: datetime


class ConversationOut(BaseModel):
    id: uuid.UUID
    title: str
    archived: bool
    context: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


class TurnOut(BaseModel):
    conversation: ConversationOut
    user: MessageOut
    assistant: MessageOut


class RunOut(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    user_id: uuid.UUID
    user_email: str | None
    question: str
    mode: str
    status: str
    intent: str | None
    prompt_version: str
    prompt_sha256: str
    provider: str | None
    model: str | None
    plan: list[Any]
    tool_calls: list[Any]
    llm_calls: list[Any]
    safety: dict[str, Any]
    source_ids: list[Any]
    answer_sha256: str | None
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None
    error: str | None
    created_at: datetime
    answer: MessageOut | None


class RunListOut(BaseModel):
    items: list[RunOut]
    total: int


class ReportOut(BaseModel):
    id: uuid.UUID
    kind: str
    title: str
    subject_id: uuid.UUID | None
    run_id: uuid.UUID | None
    conversation_id: uuid.UUID | None
    blocks: list[dict[str, Any]]
    unknowns: list[str]
    sources: list[dict[str, Any]]
    content_sha256: str
    created_by: uuid.UUID | None
    created_at: datetime


class StatusOut(BaseModel):
    mode: str
    configured_mode: str
    providers: list[str]
    prompt_version: str
    prompt_sha256: str
    glossary_version: str
    tools: list[dict[str, str]]
    indexed_documents: int
    unindexed_documents: int
