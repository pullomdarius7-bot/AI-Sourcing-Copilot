import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models import ExtractionRun, Parameter, Requirement
from app.requirements.grounding import bucket
from app.requirements.service import display_value


class EvidenceOut(BaseModel):
    source_quote: str | None
    source_start: int | None
    source_end: int | None
    source_line: int | None
    source_section: str | None
    confidence: float
    model_confidence: float
    confidence_bucket: str
    confidence_detail: dict[str, Any]
    grounding_status: str
    flags: list[Any]
    validation_state: str
    verified: bool
    verified_by: uuid.UUID | None
    verified_at: datetime | None
    review_note: str | None
    original: dict[str, Any] | None


class ParameterOut(EvidenceOut):
    id: uuid.UUID
    run_id: uuid.UUID
    requirement_id: uuid.UUID | None
    position: int
    name: str
    value_type: str
    value_text: str | None
    value_number: float | None
    unit: str | None
    qualifier: str | None
    unknown: bool
    display_value: str | None


class RequirementOut(EvidenceOut):
    id: uuid.UUID
    run_id: uuid.UUID
    position: int
    statement: str
    category: str
    priority: str
    parameters: list[ParameterOut]


class RunCounts(BaseModel):
    requirements: int = 0
    parameters: int = 0
    unknown: int = 0
    unresolved: int = 0
    verified: int = 0
    corrected: int = 0
    rejected: int = 0


class RunOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    filename: str | None = None
    status: str
    provider: str | None
    model: str | None
    prompt_version: str | None
    flags: list[Any]
    error: str | None
    created_by: uuid.UUID | None
    approved_by: uuid.UUID | None
    approved_at: datetime | None
    decision_note: str | None
    created_at: datetime
    completed_at: datetime | None
    counts: RunCounts
    source_changed: bool = False


class RunDetailOut(RunOut):
    requirements: list[RequirementOut]
    unknown_parameters: list[ParameterOut]


class EventOut(BaseModel):
    id: int
    item_type: str
    item_id: uuid.UUID
    action: str
    actor_id: uuid.UUID | None
    actor_email: str | None
    from_state: str | None
    to_state: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    note: str | None
    created_at: datetime


class QueueItemOut(BaseModel):
    item_type: str
    id: uuid.UUID
    run_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    label: str
    value: str | None
    requirement_statement: str | None
    confidence: float
    confidence_bucket: str
    flags: list[Any]
    validation_state: str
    grounding_status: str
    source_quote: str | None
    source_start: int | None
    source_end: int | None


class QueueOut(BaseModel):
    items: list[QueueItemOut]
    total: int


class TextOut(BaseModel):
    text: str
    sha256: str
    chars: int


class VerifyIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


class RejectIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class CorrectParameterIn(BaseModel):
    unknown: bool = False
    value_type: Literal["text", "number"] | None = None
    text: str | None = Field(default=None, max_length=500)
    number: float | None = None
    unit: str | None = Field(default=None, max_length=30)
    qualifier: Literal["exact", "min", "max", "approx"] | None = None
    evidence: Literal["document", "manual"] = "document"
    source_quote: str | None = Field(default=None, max_length=2000)
    note: str | None = Field(default=None, max_length=1000)


class CorrectRequirementIn(BaseModel):
    statement: str | None = Field(default=None, max_length=600)
    category: str | None = None
    priority: str | None = None
    source_quote: str | None = Field(default=None, max_length=2000)
    note: str | None = Field(default=None, max_length=1000)


class BulkVerifyIn(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=100)


class BulkVerifyOut(BaseModel):
    verified: list[uuid.UUID]
    skipped: list[dict[str, str]]


class ApproveIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


# Stored for audit only. The rejected model output must never reach clients, not even as detail.
_INTERNAL_DETAIL_KEYS = frozenset({"discarded_model_claim"})


def _evidence(item: Requirement | Parameter) -> dict[str, Any]:
    detail = {k: v for k, v in (item.confidence_detail or {}).items() if k not in _INTERNAL_DETAIL_KEYS}
    return dict(
        source_quote=item.source_quote, source_start=item.source_start, source_end=item.source_end,
        source_line=item.source_line, source_section=item.source_section, confidence=item.confidence,
        model_confidence=item.model_confidence, confidence_bucket=bucket(item.confidence),
        confidence_detail=detail, grounding_status=item.grounding_status, flags=item.flags,
        validation_state=item.validation_state, verified=item.validation_state in ("verified", "corrected"),
        verified_by=item.verified_by, verified_at=item.verified_at, review_note=item.review_note,
        original=item.original,
    )


def parameter_out(prm: Parameter) -> ParameterOut:
    return ParameterOut(
        id=prm.id, run_id=prm.run_id, requirement_id=prm.requirement_id, position=prm.position, name=prm.name,
        value_type=prm.value_type, value_text=prm.value_text, value_number=prm.value_number, unit=prm.unit,
        qualifier=prm.qualifier, unknown=prm.unknown, display_value=display_value(prm), **_evidence(prm),
    )


def requirement_out(req: Requirement, params: list[Parameter]) -> RequirementOut:
    return RequirementOut(
        id=req.id, run_id=req.run_id, position=req.position, statement=req.statement, category=req.category,
        priority=req.priority, parameters=[parameter_out(p) for p in params], **_evidence(req),
    )


def run_out(run: ExtractionRun, counts: RunCounts, filename: str | None, source_changed: bool) -> RunOut:
    return RunOut(
        id=run.id, document_id=run.document_id, filename=filename, status=run.status, provider=run.provider,
        model=run.model, prompt_version=run.prompt_version, flags=run.flags, error=run.error,
        created_by=run.created_by, approved_by=run.approved_by, approved_at=run.approved_at,
        decision_note=run.decision_note, created_at=run.created_at, completed_at=run.completed_at,
        counts=counts, source_changed=source_changed,
    )
