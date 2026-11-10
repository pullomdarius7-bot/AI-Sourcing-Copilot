import re
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record
from app.config import get_settings
from app.db import get_db
from app.deps import Principal, require_permission
from app.errors import ApiError, conflict, not_found
from app.files.sniff import ALLOWED_EXTENSIONS
from app.jobs.handlers import enqueue_pipeline
from app.models import Document, DocumentExtraction, Job
from app.rbac import Permission
from app.schemas import (
    AnalysisOut,
    DocumentDetailOut,
    DocumentListOut,
    DocumentOut,
    JobBrief,
    MessageOut,
    UploadLimitsOut,
    UploadOut,
)
from app.services import documents as svc
from app.services.auth import utcnow
from app.storage import StorageError, get_storage

settings = get_settings()
router = APIRouter(prefix="/documents", tags=["documents"])

STATUSES = {"uploaded", "scanning", "extracting", "analyzing", "processed", "quarantined", "failed"}


async def _get_doc(db: AsyncSession, p: Principal, doc_id: uuid.UUID) -> Document:
    doc = await db.scalar(select(Document).where(Document.id == doc_id, Document.organization_id == p.org_id))
    if doc is None:
        raise not_found("Document not found")
    return doc


@router.get("/limits", response_model=UploadLimitsOut)
async def limits(_: Principal = Depends(require_permission(Permission.DOCUMENTS_READ))):
    return UploadLimitsOut(max_upload_mb=settings.max_upload_mb, extensions=sorted(ALLOWED_EXTENSIONS))


@router.post("", response_model=UploadOut)
async def upload(
    response: Response,
    file: UploadFile = File(...),
    p: Principal = Depends(require_permission(Permission.DOCUMENTS_UPLOAD)),
    db: AsyncSession = Depends(get_db),
):
    doc, duplicate = await svc.upload_document(db, p, file)
    response.status_code = 200 if duplicate else 201
    return UploadOut(document=DocumentOut.model_validate(doc), duplicate=duplicate)


@router.get("", response_model=DocumentListOut)
async def list_documents(
    status: str | None = Query(None),
    q: str | None = Query(None, max_length=100),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    p: Principal = Depends(require_permission(Permission.DOCUMENTS_READ)),
    db: AsyncSession = Depends(get_db),
):
    if status and status not in STATUSES:
        raise ApiError(422, "validation_error", "Unknown status filter")
    conditions = [Document.organization_id == p.org_id]
    if status:
        conditions.append(Document.status == status)
    if q:
        pattern = "%" + re.sub(r"([\\%_])", r"\\\1", q.strip()) + "%"
        conditions.append(Document.filename.ilike(pattern, escape="\\"))
    total = await db.scalar(select(func.count()).select_from(Document).where(*conditions))
    rows = await db.scalars(
        select(Document).where(*conditions).order_by(Document.created_at.desc(), Document.id).limit(limit).offset(offset)
    )
    return DocumentListOut(items=[DocumentOut.model_validate(d) for d in rows], total=total or 0)


@router.get("/{doc_id}", response_model=DocumentDetailOut)
async def get_document(
    doc_id: uuid.UUID,
    p: Principal = Depends(require_permission(Permission.DOCUMENTS_READ)),
    db: AsyncSession = Depends(get_db),
):
    doc = await _get_doc(db, p, doc_id)
    jobs = await db.scalars(
        select(Job)
        .where(Job.organization_id == p.org_id, Job.payload["document_id"].astext == str(doc.id))
        .order_by(Job.created_at)
    )
    data = DocumentOut.model_validate(doc).model_dump()
    return DocumentDetailOut(
        **data,
        text_preview=(doc.extracted_text or "")[:2000] or None,
        jobs=[JobBrief.model_validate(j) for j in jobs],
    )


@router.get("/{doc_id}/analysis", response_model=AnalysisOut)
async def get_analysis(
    doc_id: uuid.UUID,
    p: Principal = Depends(require_permission(Permission.DOCUMENTS_READ)),
    db: AsyncSession = Depends(get_db),
):
    doc = await _get_doc(db, p, doc_id)
    row = await db.scalar(
        select(DocumentExtraction)
        .where(DocumentExtraction.document_id == doc.id, DocumentExtraction.organization_id == p.org_id)
        .order_by(DocumentExtraction.created_at.desc())
        .limit(1)
    )
    if row is None:
        raise not_found("No analysis available for this document")
    return row


@router.get("/{doc_id}/download")
async def download(
    doc_id: uuid.UUID,
    p: Principal = Depends(require_permission(Permission.DOCUMENTS_READ)),
    db: AsyncSession = Depends(get_db),
):
    doc = await _get_doc(db, p, doc_id)
    if doc.scan_status != "clean" or not doc.storage_key or not doc.storage_backend:
        raise conflict("not_available", "The file is not available for download (it has not passed the virus scan)")
    try:
        data = await get_storage().get(doc.storage_backend, doc.storage_key)
    except StorageError:
        raise ApiError(503, "storage_unavailable", "File storage is unavailable. Try again shortly.") from None
    record(db, org_id=p.org_id, user_id=p.user.id, action="document.downloaded", ip=p.ip, meta={"document_id": str(doc.id)})
    await db.commit()
    return Response(
        content=data,
        media_type=doc.content_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(doc.filename)}",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


@router.post("/{doc_id}/reprocess", response_model=DocumentOut)
async def reprocess(
    doc_id: uuid.UUID,
    p: Principal = Depends(require_permission(Permission.DOCUMENTS_UPLOAD)),
    db: AsyncSession = Depends(get_db),
):
    doc = await _get_doc(db, p, doc_id)
    if doc.status not in ("failed", "processed") or not doc.storage_key:
        raise conflict("not_reprocessable", "Only processed or failed documents can be reprocessed")
    doc.status, doc.scan_status, doc.analysis_status = "uploaded", "pending", "pending"
    doc.error, doc.processed_at, doc.updated_at = None, None, utcnow()
    job = await enqueue_pipeline(db, doc)
    if job is None:
        await db.rollback()
        raise conflict("already_processing", "This document is already being processed")
    record(db, org_id=p.org_id, user_id=p.user.id, action="document.reprocess", ip=p.ip, meta={"document_id": str(doc.id)})
    await db.commit()
    return doc


@router.delete("/{doc_id}", response_model=MessageOut)
async def delete_document(
    doc_id: uuid.UUID,
    p: Principal = Depends(require_permission(Permission.DOCUMENTS_DELETE)),
    db: AsyncSession = Depends(get_db),
):
    doc = await _get_doc(db, p, doc_id)
    backend, key = doc.storage_backend, doc.storage_key
    await db.execute(
        delete(Job).where(Job.organization_id == p.org_id, Job.status == "queued", Job.payload["document_id"].astext == str(doc.id))
    )
    await db.execute(delete(DocumentExtraction).where(DocumentExtraction.document_id == doc.id))
    await db.delete(doc)
    record(
        db, org_id=p.org_id, user_id=p.user.id, action="document.deleted", ip=p.ip,
        meta={"document_id": str(doc_id), "filename": doc.filename, "sha256": doc.sha256},
    )
    await db.commit()
    if backend and key:
        try:
            await get_storage().delete(backend, key)
        except StorageError:
            pass  # object is orphaned but unreachable; the row is gone
    return MessageOut(message="Document deleted")
