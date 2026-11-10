import uuid
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.service import get_ai_service
from app.db import get_db, set_tenant
from app.deps import Principal, require_permission
from app.errors import conflict, not_found
from app.jobs import queue
from app.models import Document, ExtractionRun, Parameter, Requirement, ReviewEvent, User
from app.rbac import Permission
from app.requirements import service as svc
from app.requirements.api_schemas import (
    ApproveIn,
    BulkVerifyIn,
    BulkVerifyOut,
    CorrectParameterIn,
    CorrectRequirementIn,
    EventOut,
    ParameterOut,
    QueueItemOut,
    QueueOut,
    RejectIn,
    RequirementOut,
    RunCounts,
    RunDetailOut,
    RunOut,
    TextOut,
    VerifyIn,
    parameter_out,
    requirement_out,
    run_out,
)
from app.requirements.grounding import bucket

router = APIRouter(tags=["requirements"])
READ = Permission.REQUIREMENTS_READ
EXTRACT = Permission.REQUIREMENTS_EXTRACT
REVIEW = Permission.REQUIREMENTS_REVIEW
APPROVE = Permission.REQUIREMENTS_APPROVE
RUN_STATUSES = "^(extracting|pending_review|ready_for_approval|approved|rejected|failed|superseded)$"


async def _doc(db: AsyncSession, p: Principal, doc_id: uuid.UUID) -> Document:
    doc = await db.scalar(select(Document).where(Document.id == doc_id, Document.organization_id == p.org_id))
    if doc is None:
        raise not_found("Document not found")
    return doc


async def _counts(db: AsyncSession, run_ids: list[uuid.UUID]) -> dict[uuid.UUID, RunCounts]:
    out = {rid: RunCounts() for rid in run_ids}
    if not run_ids:
        return out
    for rid, state, n in (await db.execute(
        select(Requirement.run_id, Requirement.validation_state, func.count()).where(Requirement.run_id.in_(run_ids)).group_by(Requirement.run_id, Requirement.validation_state)
    )).all():
        c = out[rid]
        c.requirements += n
        if state == "unverified":
            c.unresolved += n
    for rid, state, unknown, n in (await db.execute(
        select(Parameter.run_id, Parameter.validation_state, Parameter.unknown, func.count()).where(Parameter.run_id.in_(run_ids)).group_by(Parameter.run_id, Parameter.validation_state, Parameter.unknown)
    )).all():
        c = out[rid]
        c.parameters += n
        c.unknown += n if unknown else 0
        if state == "unverified":
            c.unresolved += n
        elif state in ("verified", "corrected", "rejected"):
            setattr(c, state, getattr(c, state) + n)
    return out


async def _summaries(db: AsyncSession, runs: list[ExtractionRun]) -> list[RunOut]:
    if runs:
        await set_tenant(db, runs[0].organization_id)  # services commit, which ends the tenant-scoped transaction
    counts = await _counts(db, [r.id for r in runs])
    docs = {
        d.id: d for d in await db.scalars(select(Document).where(Document.id.in_({r.document_id for r in runs})))
    } if runs else {}
    result = []
    for run in runs:
        doc = docs.get(run.document_id)
        changed = bool(run.source_text_sha256 and doc and svc.sha(doc.extracted_text or "") != run.source_text_sha256)
        result.append(run_out(run, counts[run.id], doc.filename if doc else None, changed))
    return result


async def _run(db: AsyncSession, p: Principal, run_id: uuid.UUID) -> ExtractionRun:
    run = await db.scalar(select(ExtractionRun).where(ExtractionRun.id == run_id, ExtractionRun.organization_id == p.org_id))
    if run is None:
        raise not_found("Extraction run not found")
    return run


# --- Document text & extraction ------------------------------------------------------------
@router.get("/documents/{doc_id}/text", response_model=TextOut)
async def document_text(doc_id: uuid.UUID, p: Principal = Depends(require_permission(READ)), db: AsyncSession = Depends(get_db)):
    doc = await _doc(db, p, doc_id)
    if doc.scan_status != "clean" or doc.extracted_text is None:
        raise not_found("No text is available for this document")
    return TextOut(text=doc.extracted_text, sha256=svc.sha(doc.extracted_text), chars=len(doc.extracted_text))


@router.post("/documents/{doc_id}/requirements/extract", response_model=RunOut, status_code=202)
async def start_extraction(doc_id: uuid.UUID, p: Principal = Depends(require_permission(EXTRACT)), db: AsyncSession = Depends(get_db)):
    doc = await _doc(db, p, doc_id)
    if doc.scan_status != "clean" or doc.status not in ("processed", "analyzing") or not (doc.extracted_text or "").strip():
        raise conflict("not_ready", "The document must be scanned and have extractable text first")
    if not get_ai_service().providers:
        raise conflict("no_ai_provider", "No AI provider is configured")
    run = await svc.create_run(db, p, doc)
    job = await queue.enqueue(
        db, org_id=p.org_id, kind="requirements.extract", payload={"run_id": str(run.id), "document_id": str(doc.id)},
        dedupe_key=f"requirements.extract:{doc.id}", max_attempts=3,
    )
    if job is None:
        await db.rollback()
        raise conflict("already_running", "An extraction is already running for this document")
    await db.refresh(run)
    await db.commit()
    return (await _summaries(db, [run]))[0]


@router.get("/extraction-runs", response_model=list[RunOut])
async def list_runs(
    status: str | None = Query(None, pattern=RUN_STATUSES), document_id: uuid.UUID | None = None,
    limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0),
    p: Principal = Depends(require_permission(READ)), db: AsyncSession = Depends(get_db),
):
    stmt = select(ExtractionRun).where(ExtractionRun.organization_id == p.org_id)
    if status:
        stmt = stmt.where(ExtractionRun.status == status)
    if document_id:
        stmt = stmt.where(ExtractionRun.document_id == document_id)
    runs = list(await db.scalars(stmt.order_by(ExtractionRun.created_at.desc()).limit(limit).offset(offset)))
    return await _summaries(db, runs)


@router.get("/extraction-runs/{run_id}", response_model=RunDetailOut)
async def get_run(run_id: uuid.UUID, p: Principal = Depends(require_permission(READ)), db: AsyncSession = Depends(get_db)):
    run = await _run(db, p, run_id)
    summary = (await _summaries(db, [run]))[0]
    reqs = list(await db.scalars(select(Requirement).where(Requirement.run_id == run.id).order_by(Requirement.position)))
    params = list(await db.scalars(select(Parameter).where(Parameter.run_id == run.id).order_by(Parameter.position)))
    by_req: dict[uuid.UUID | None, list[Parameter]] = defaultdict(list)
    for prm in params:
        by_req[prm.requirement_id].append(prm)
    return RunDetailOut(
        **summary.model_dump(),
        requirements=[requirement_out(r, by_req[r.id]) for r in reqs],
        unknown_parameters=[parameter_out(x) for x in by_req[None]],
    )


@router.get("/extraction-runs/{run_id}/events", response_model=list[EventOut])
async def run_events(run_id: uuid.UUID, p: Principal = Depends(require_permission(READ)), db: AsyncSession = Depends(get_db)):
    run = await _run(db, p, run_id)
    rows = (await db.execute(
        select(ReviewEvent, User.email).join(User, User.id == ReviewEvent.actor_id, isouter=True)
        .where(ReviewEvent.run_id == run.id, ReviewEvent.organization_id == p.org_id)
        .order_by(ReviewEvent.id.desc()).limit(500)
    )).all()
    return [
        EventOut(id=e.id, item_type=e.item_type, item_id=e.item_id, action=e.action, actor_id=e.actor_id,
                 actor_email=email, from_state=e.from_state, to_state=e.to_state, before=e.before, after=e.after,
                 note=e.note, created_at=e.created_at)
        for e, email in rows
    ]


@router.get("/extraction-runs/{run_id}/export")
async def export_run(run_id: uuid.UUID, p: Principal = Depends(require_permission(READ)), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    run = await _run(db, p, run_id)
    if run.status != "approved":
        raise conflict("not_approved", "Only approved extractions can be exported")
    doc = await db.get(Document, run.document_id)
    detail = await get_run(run_id, p, db)

    def prm(x: ParameterOut) -> dict[str, Any]:
        return {
            "name": x.name, "unknown": x.unknown, "value": None if x.unknown else (x.value_number if x.value_type == "number" else x.value_text),
            "unit": x.unit, "qualifier": x.qualifier, "confidence": x.confidence,
            "validation_state": x.validation_state, "verified": x.verified, "grounding": x.grounding_status,
            "source": {"quote": x.source_quote, "start": x.source_start, "end": x.source_end, "line": x.source_line, "section": x.source_section},
        }

    return {
        "run_id": str(run.id), "approved_at": run.approved_at, "approved_by": str(run.approved_by),
        "document": {"id": str(run.document_id), "filename": doc.filename if doc else None, "sha256": doc.sha256 if doc else None},
        "requirements": [
            {"statement": r.statement, "category": r.category, "priority": r.priority, "confidence": r.confidence,
             "validation_state": r.validation_state,
             "source": {"quote": r.source_quote, "start": r.source_start, "end": r.source_end, "line": r.source_line, "section": r.source_section},
             "parameters": [prm(x) for x in r.parameters if x.validation_state != "rejected"]}
            for r in detail.requirements if r.validation_state != "rejected"
        ],
        "unknown_parameters": [prm(x) for x in detail.unknown_parameters if x.validation_state != "rejected"],
    }


@router.post("/extraction-runs/{run_id}/approve", response_model=RunOut)
async def approve(run_id: uuid.UUID, body: ApproveIn, p: Principal = Depends(require_permission(APPROVE)), db: AsyncSession = Depends(get_db)):
    run = await svc.approve_run(db, p, run_id, body.note)
    return (await _summaries(db, [run]))[0]


@router.post("/extraction-runs/{run_id}/reject", response_model=RunOut)
async def reject_run(run_id: uuid.UUID, body: RejectIn, p: Principal = Depends(require_permission(APPROVE)), db: AsyncSession = Depends(get_db)):
    run = await svc.reject_run(db, p, run_id, body.reason)
    return (await _summaries(db, [run]))[0]


# --- Item review ----------------------------------------------------------------------------------
@router.post("/parameters/bulk-verify", response_model=BulkVerifyOut)
async def bulk_verify(body: BulkVerifyIn, p: Principal = Depends(require_permission(REVIEW)), db: AsyncSession = Depends(get_db)):
    return await svc.bulk_verify(db, p, body.ids)


@router.post("/parameters/{item_id}/verify", response_model=ParameterOut)
async def verify_parameter(item_id: uuid.UUID, body: VerifyIn, p: Principal = Depends(require_permission(REVIEW)), db: AsyncSession = Depends(get_db)):
    return parameter_out(await svc.verify(db, p, "parameter", item_id, body.note))  # type: ignore[arg-type]


@router.post("/parameters/{item_id}/correct", response_model=ParameterOut)
async def correct_parameter(item_id: uuid.UUID, body: CorrectParameterIn, p: Principal = Depends(require_permission(REVIEW)), db: AsyncSession = Depends(get_db)):
    return parameter_out(await svc.correct_parameter(db, p, item_id, body))


@router.post("/parameters/{item_id}/reject", response_model=ParameterOut)
async def reject_parameter(item_id: uuid.UUID, body: RejectIn, p: Principal = Depends(require_permission(REVIEW)), db: AsyncSession = Depends(get_db)):
    return parameter_out(await svc.reject(db, p, "parameter", item_id, body.reason))  # type: ignore[arg-type]


@router.post("/parameters/{item_id}/reset", response_model=ParameterOut)
async def reset_parameter(item_id: uuid.UUID, p: Principal = Depends(require_permission(REVIEW)), db: AsyncSession = Depends(get_db)):
    return parameter_out(await svc.reset(db, p, "parameter", item_id))  # type: ignore[arg-type]


async def _req_out(db: AsyncSession, req: Requirement) -> RequirementOut:
    await set_tenant(db, req.organization_id)
    params = list(await db.scalars(select(Parameter).where(Parameter.requirement_id == req.id).order_by(Parameter.position)))
    return requirement_out(req, params)


@router.post("/requirements/{item_id}/verify", response_model=RequirementOut)
async def verify_requirement(item_id: uuid.UUID, body: VerifyIn, p: Principal = Depends(require_permission(REVIEW)), db: AsyncSession = Depends(get_db)):
    return await _req_out(db, await svc.verify(db, p, "requirement", item_id, body.note))  # type: ignore[arg-type]


@router.post("/requirements/{item_id}/correct", response_model=RequirementOut)
async def correct_requirement(item_id: uuid.UUID, body: CorrectRequirementIn, p: Principal = Depends(require_permission(REVIEW)), db: AsyncSession = Depends(get_db)):
    return await _req_out(db, await svc.correct_requirement(db, p, item_id, body))


@router.post("/requirements/{item_id}/reject", response_model=RequirementOut)
async def reject_requirement(item_id: uuid.UUID, body: RejectIn, p: Principal = Depends(require_permission(REVIEW)), db: AsyncSession = Depends(get_db)):
    return await _req_out(db, await svc.reject(db, p, "requirement", item_id, body.reason))  # type: ignore[arg-type]


@router.post("/requirements/{item_id}/reset", response_model=RequirementOut)
async def reset_requirement(item_id: uuid.UUID, p: Principal = Depends(require_permission(REVIEW)), db: AsyncSession = Depends(get_db)):
    return await _req_out(db, await svc.reset(db, p, "requirement", item_id))  # type: ignore[arg-type]


# --- Review queue ------------------------------------------------------------------------------------
@router.get("/review/queue", response_model=QueueOut)
async def review_queue(
    max_confidence: float | None = Query(None, ge=0, le=1), flagged: bool | None = None,
    document_id: uuid.UUID | None = None, run_id: uuid.UUID | None = None,
    item_type: str | None = Query(None, pattern="^(parameter|requirement)$"),
    limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0),
    p: Principal = Depends(require_permission(REVIEW)), db: AsyncSession = Depends(get_db),
):
    """Unverified items of runs awaiting review, riskiest first (flagged, then lowest confidence)."""
    items: list[QueueItemOut] = []
    total = 0
    for kind, model in (("requirement", Requirement), ("parameter", Parameter)):
        if item_type and item_type != kind:
            continue
        conds = [
            model.organization_id == p.org_id, model.validation_state == "unverified",
            ExtractionRun.status == "pending_review",
        ]
        if max_confidence is not None:
            conds.append(model.confidence <= max_confidence)
        if flagged is True:
            conds.append(func.jsonb_array_length(model.flags) > 0)
        elif flagged is False:
            conds.append(func.jsonb_array_length(model.flags) == 0)
        if document_id:
            conds.append(model.document_id == document_id)
        if run_id:
            conds.append(model.run_id == run_id)
        base = select(model, Document.filename).join(ExtractionRun, ExtractionRun.id == model.run_id).join(Document, Document.id == model.document_id).where(*conds)
        total += await db.scalar(select(func.count()).select_from(base.subquery())) or 0
        rows = (await db.execute(
            base.order_by((func.jsonb_array_length(model.flags) > 0).desc(), model.confidence.asc(), model.id).limit(limit + offset)
        )).all()
        for item, filename in rows:
            if kind == "requirement":
                items.append(QueueItemOut(
                    item_type=kind, id=item.id, run_id=item.run_id, document_id=item.document_id, filename=filename,
                    label=f"{item.category} · {item.priority}", value=item.statement, requirement_statement=None,
                    confidence=item.confidence, confidence_bucket=bucket(item.confidence), flags=item.flags,
                    validation_state=item.validation_state, grounding_status=item.grounding_status,
                    source_quote=item.source_quote, source_start=item.source_start, source_end=item.source_end,
                ))
            else:
                statement = None
                if item.requirement_id:
                    statement = await db.scalar(select(Requirement.statement).where(Requirement.id == item.requirement_id))
                items.append(QueueItemOut(
                    item_type=kind, id=item.id, run_id=item.run_id, document_id=item.document_id, filename=filename,
                    label=item.name, value=svc.display_value(item), requirement_statement=statement,
                    confidence=item.confidence, confidence_bucket=bucket(item.confidence), flags=item.flags,
                    validation_state=item.validation_state, grounding_status=item.grounding_status,
                    source_quote=item.source_quote, source_start=item.source_start, source_end=item.source_end,
                ))
    items.sort(key=lambda i: (0 if i.flags else 1, i.confidence))
    return QueueOut(items=items[offset : offset + limit], total=total)
