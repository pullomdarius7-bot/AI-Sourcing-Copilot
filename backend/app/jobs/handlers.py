"""Document pipeline: scan -> extract -> analyze. Each step is its own retryable job."""
import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.retrieval import index_document
from app.ai.service import get_ai_service
from app.audit import record
from app.db import set_tenant
from app.files import extract as extractor
from app.files import scan as scanner
from app.jobs import queue
from app.jobs.errors import PermanentError, RetryableError
from app.models import Document, ExtractionRun, Job
from app.storage import StorageError, get_storage

logger = logging.getLogger("app.jobs")

SCAN = "document.scan"
EXTRACT = "document.extract"
ANALYZE = "document.analyze"
REQUIREMENTS = "requirements.extract"
MAX_VISION_BYTES = 5 * 1024 * 1024


def _now() -> datetime:
    return datetime.now(UTC)


async def enqueue_pipeline(db: AsyncSession, doc: Document) -> None:
    await queue.enqueue(
        db, org_id=doc.organization_id, kind=SCAN, payload={"document_id": str(doc.id)},
        dedupe_key=f"{SCAN}:{doc.id}",
    )


async def _load(db: AsyncSession, job: Job) -> Document:
    doc = await db.get(Document, uuid.UUID(job.payload["document_id"]))
    if doc is None:
        raise PermanentError("The document no longer exists")
    return doc


async def _read(doc: Document) -> bytes:
    if not doc.storage_backend or not doc.storage_key:
        raise PermanentError("The document has no stored content")
    try:
        return await get_storage().get(doc.storage_backend, doc.storage_key)
    except StorageError as exc:
        raise RetryableError(str(exc)) from exc


async def handle_scan(db: AsyncSession, job: Job) -> dict[str, Any]:
    doc = await _load(db, job)
    if doc.status == "quarantined":
        return {"skipped": "already quarantined"}
    doc.status, doc.updated_at = "scanning", _now()
    await db.commit()
    await set_tenant(db, doc.organization_id)

    data = await _read(doc)
    try:
        result = await scanner.scan(data, doc.content_type)
    except scanner.ScanUnavailable as exc:
        raise RetryableError(str(exc)) from exc

    if not result.clean:
        doc.status, doc.scan_status = "quarantined", "infected"
        doc.scan_engine, doc.scan_detail = result.engine, (result.signature or "")[:200]
        doc.updated_at = _now()
        backend, key = doc.storage_backend, doc.storage_key
        doc.storage_key = None  # blocks download and reprocessing
        record(
            db, org_id=doc.organization_id, user_id=doc.uploaded_by, action="document.quarantined",
            meta={"document_id": str(doc.id), "signature": result.signature, "engine": result.engine},
        )
        await db.commit()
        try:
            await get_storage().delete(backend, key)  # type: ignore[arg-type]
        except StorageError:
            logger.warning("could not delete infected object %s", key)
        return {"infected": result.signature}

    doc.scan_status, doc.scan_engine, doc.scan_detail = "clean", result.engine, None
    doc.status, doc.updated_at = "extracting", _now()
    await queue.enqueue(
        db, org_id=doc.organization_id, kind=EXTRACT, payload={"document_id": str(doc.id)},
        dedupe_key=f"{EXTRACT}:{doc.id}",
    )
    await db.commit()
    return {"clean": True, "engine": result.engine}


async def handle_extract(db: AsyncSession, job: Job) -> dict[str, Any]:
    doc = await _load(db, job)
    if doc.scan_status != "clean":
        raise PermanentError("Refusing to process a file that has not passed the virus scan")
    data = await _read(doc)
    try:
        extracted = await asyncio.to_thread(extractor.extract, doc.content_type, data)
    except extractor.ExtractionError as exc:
        raise PermanentError(str(exc)) from exc
    doc.file_metadata = {**extracted.metadata, "text_chars": len(extracted.text)}
    doc.extracted_text = extracted.text or None
    doc.status, doc.analysis_status, doc.updated_at = "analyzing", "pending", _now()
    await queue.enqueue(
        db, org_id=doc.organization_id, kind=ANALYZE, payload={"document_id": str(doc.id)},
        dedupe_key=f"{ANALYZE}:{doc.id}",
    )
    await db.commit()
    return {"text_chars": len(extracted.text)}


async def handle_analyze(db: AsyncSession, job: Job) -> dict[str, Any]:
    doc = await _load(db, job)
    if doc.scan_status != "clean":
        raise PermanentError("Refusing to analyze a file that has not passed the virus scan")
    images: list[tuple[str, bytes]] = []
    if doc.content_type.startswith("image/") and doc.size_bytes <= MAX_VISION_BYTES:
        images.append((doc.content_type, await _read(doc)))
    outcome = await get_ai_service().analyze_document(db, doc, images=images, user_id=doc.uploaded_by)
    doc.analysis_status = outcome.status
    if outcome.status == "skipped":
        doc.file_metadata = {**doc.file_metadata, "analysis_skipped_reason": outcome.reason}
    doc.status, doc.error = "processed", None
    doc.processed_at = doc.updated_at = _now()
    passages = await index_document(db, doc)  # searchable by the sourcing agent from now on
    record(
        db, org_id=doc.organization_id, user_id=doc.uploaded_by, action="document.processed",
        meta={"document_id": str(doc.id), "analysis": outcome.status, "passages": passages},
    )
    await db.commit()
    return {"analysis": outcome.status}


async def _doc_for_hook(db: AsyncSession, job: Job) -> Document | None:
    try:
        return await db.get(Document, uuid.UUID(job.payload["document_id"]))
    except (KeyError, ValueError):
        return None


async def dead_pipeline_step(db: AsyncSession, job: Job, error: str) -> None:
    doc = await _doc_for_hook(db, job)
    if doc is None or doc.status == "quarantined":
        return
    doc.status, doc.error, doc.updated_at = "failed", error[:2000], _now()
    record(
        db, org_id=doc.organization_id, user_id=doc.uploaded_by, action="document.failed",
        meta={"document_id": str(doc.id), "step": job.kind, "error": error[:300]},
    )
    await db.commit()


async def dead_analyze(db: AsyncSession, job: Job, error: str) -> None:
    doc = await _doc_for_hook(db, job)
    if doc is None:
        return
    doc.status, doc.analysis_status = "processed", "failed"
    doc.error, doc.processed_at, doc.updated_at = error[:2000], _now(), _now()
    await index_document(db, doc)  # the text is still useful even though the AI analysis failed
    record(
        db, org_id=doc.organization_id, user_id=doc.uploaded_by, action="document.analysis_failed",
        meta={"document_id": str(doc.id), "error": error[:300]},
    )
    await db.commit()


async def handle_requirements(db: AsyncSession, job: Job) -> dict[str, Any]:
    from app.requirements import service as req_service

    run = await db.get(ExtractionRun, uuid.UUID(job.payload["run_id"]))
    if run is None:
        raise PermanentError("The extraction run no longer exists")
    if run.status != "extracting":
        return {"skipped": f"run is {run.status}"}
    doc = await db.get(Document, run.document_id)
    if doc is None or doc.scan_status != "clean":
        raise PermanentError("The document is unavailable or has not passed the virus scan")
    return await req_service.execute_run(db, run, doc)


async def dead_requirements(db: AsyncSession, job: Job, error: str) -> None:
    from app.requirements import service as req_service

    try:
        run = await db.get(ExtractionRun, uuid.UUID(job.payload["run_id"]))
    except (KeyError, ValueError):
        return
    if run is not None and run.status == "extracting":
        await req_service.fail_run(db, run, error)


SEND_PACKAGE = "rfq.send_package"


async def handle_send_package(db: AsyncSession, job: Job) -> dict[str, Any]:
    """Send a stored RFQ package exactly as approved: verify integrity and approval binding first."""
    import smtplib

    from sqlalchemy import select

    from app.email import send_message
    from app.events import emit_event
    from app.models import Rfq, RfqPackage, RfqSupplier
    from app.rfqs.package import parse_package, verify_package

    pkg = await db.get(RfqPackage, uuid.UUID(job.payload["package_id"]))
    if pkg is None:
        raise PermanentError("The package no longer exists")
    if pkg.send_status == "sent":
        return {"skipped": "already sent"}
    rfq = await db.get(Rfq, pkg.rfq_id)
    if rfq is None or rfq.status == "cancelled":
        raise PermanentError("The RFQ was cancelled")
    if rfq.status not in ("sent", "closed") or pkg.rfq_version != rfq.version:
        raise PermanentError("The package does not belong to the RFQ version that was sent")
    if pkg.manifest.get("content_sha256") != rfq.content_sha256:
        raise PermanentError("The package does not match the approved RFQ content")
    try:
        raw = await get_storage().get(pkg.storage_backend, pkg.storage_key)
    except StorageError as exc:
        raise RetryableError(str(exc)) from exc
    if not verify_package(raw, pkg.sha256):
        raise PermanentError("Package integrity check failed: the stored message was modified")
    try:
        await send_message(parse_package(raw))
    except (OSError, smtplib.SMTPException) as exc:
        raise RetryableError(f"Mail delivery failed: {exc}") from exc
    pkg.send_status, pkg.sent_at, pkg.send_error = "sent", datetime.now(UTC), None
    link = await db.scalar(select(RfqSupplier).where(RfqSupplier.rfq_id == rfq.id, RfqSupplier.supplier_id == pkg.supplier_id))
    if link is not None and link.status in ("sending", "failed", "invited"):
        link.status, link.sent_at = "sent", pkg.sent_at
    emit_event(db, org_id=rfq.organization_id, entity_type="rfq", entity_id=rfq.id, action="package_sent", actor=None,
               data={"package_id": str(pkg.id), "supplier_id": str(pkg.supplier_id), "to": pkg.to_addresses})
    await db.commit()
    return {"sent_to": pkg.to_addresses}


async def dead_send_package(db: AsyncSession, job: Job, error: str) -> None:
    from sqlalchemy import select

    from app.events import emit_event
    from app.models import RfqPackage, RfqSupplier

    try:
        pkg = await db.get(RfqPackage, uuid.UUID(job.payload["package_id"]))
    except (KeyError, ValueError):
        return
    if pkg is None or pkg.send_status == "sent":
        return
    pkg.send_status, pkg.send_error = "failed", error[:2000]
    link = await db.scalar(select(RfqSupplier).where(RfqSupplier.rfq_id == pkg.rfq_id, RfqSupplier.supplier_id == pkg.supplier_id))
    if link is not None and link.status == "sending":
        link.status = "failed"
    emit_event(db, org_id=pkg.organization_id, entity_type="rfq", entity_id=pkg.rfq_id, action="package_failed", actor=None,
               data={"package_id": str(pkg.id), "supplier_id": str(pkg.supplier_id)}, note=error[:300])
    await db.commit()


Handler = Callable[[AsyncSession, Job], Awaitable[dict[str, Any] | None]]
HANDLERS: dict[str, Handler] = {
    SCAN: handle_scan, EXTRACT: handle_extract, ANALYZE: handle_analyze, REQUIREMENTS: handle_requirements,
    SEND_PACKAGE: handle_send_package,
}
ON_DEAD: dict[str, Callable[[AsyncSession, Job, str], Awaitable[None]]] = {
    SCAN: dead_pipeline_step, EXTRACT: dead_pipeline_step, ANALYZE: dead_analyze, REQUIREMENTS: dead_requirements,
    SEND_PACKAGE: dead_send_package,
}
