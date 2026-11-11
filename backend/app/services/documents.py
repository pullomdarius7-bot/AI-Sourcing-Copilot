import hashlib
import re
import unicodedata

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record
from app.config import get_settings
from app.deps import Principal
from app.errors import ApiError, bad_request
from app.files.sniff import MediaTypeError, validate_upload
from app.jobs.handlers import enqueue_pipeline
from app.models import Document
from app.storage import StorageError, get_storage

settings = get_settings()


def sanitize_filename(name: str) -> str:
    name = name.replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in unicodedata.normalize("NFC", name) if unicodedata.category(ch)[0] != "C")
    name = re.sub(r"\s+", " ", name).strip().strip(".")
    if len(name) > 255:
        stem, dot, ext = name.rpartition(".")
        name = (stem[: 250 - len(ext)] + dot + ext) if dot else name[:255]
    return name


async def read_limited(upload: UploadFile) -> bytes:
    limit = settings.max_upload_bytes
    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(1024 * 1024):
        total += len(chunk)
        if total > limit:
            raise ApiError(413, "file_too_large", f"The file exceeds the {settings.max_upload_mb} MB limit")
        chunks.append(chunk)
    return b"".join(chunks)


async def upload_document(db: AsyncSession, p: Principal, upload: UploadFile) -> tuple[Document, bool]:
    filename = sanitize_filename(upload.filename or "")
    if not filename:
        raise bad_request("invalid_filename", "The file needs a name")
    data = await read_limited(upload)
    if not data:
        raise bad_request("empty_file", "The file is empty")
    try:
        detected = validate_upload(filename, upload.content_type, data)
    except MediaTypeError as exc:
        status = 415 if exc.code == "unsupported_media_type" else 422
        raise ApiError(status, exc.code, exc.message) from None

    digest = hashlib.sha256(data).hexdigest()
    org_id = p.org_id

    async def existing_doc() -> Document | None:
        return await db.scalar(
            select(Document).where(Document.organization_id == org_id, Document.sha256 == digest)
        )

    found = await existing_doc()
    if found is None:
        try:
            backend = await get_storage().put(f"{org_id}/{digest}", data, detected.content_type)
        except StorageError as exc:
            raise ApiError(503, "storage_unavailable", "File storage is unavailable. Try again shortly.") from exc
        doc = Document(
            organization_id=org_id, uploaded_by=p.user.id, filename=filename,
            content_type=detected.content_type, size_bytes=len(data), sha256=digest,
            storage_backend=backend, storage_key=f"{org_id}/{digest}", status="uploaded",
        )
        try:
            async with db.begin_nested():
                db.add(doc)
                await db.flush()
        except IntegrityError:
            found = await existing_doc()  # lost a race with an identical concurrent upload
        else:
            await enqueue_pipeline(db, doc)
            record(
                db, org_id=org_id, user_id=p.user.id, action="document.uploaded", ip=p.ip,
                meta={"document_id": str(doc.id), "filename": filename, "size": len(data), "sha256": digest},
            )
            await db.refresh(doc)
            await db.commit()
            return doc, False

    assert found is not None
    record(
        db, org_id=org_id, user_id=p.user.id, action="document.duplicate_detected", ip=p.ip,
        meta={"document_id": str(found.id), "filename": filename, "sha256": digest},
    )
    await db.commit()
    return found, True
