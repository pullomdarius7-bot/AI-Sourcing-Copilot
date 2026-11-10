import json
import re
import uuid
from urllib.parse import quote as urlquote

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record
from app.db import get_db, set_tenant
from app.deps import Principal, require_permission
from app.errors import ApiError, not_found
from app.models import (
    Comparison,
    Rfq,
    RfqPackage,
    RfqQuote,
    RfqQuoteLine,
    RfqSupplier,
    Supplier,
    SupplierCategory,
    SupplierContact,
    User,
)
from app.rbac import Permission
from app.rfqs import comparison as cmp
from app.rfqs import render, xlsx
from app.rfqs import schemas as rs
from app.rfqs import service as svc
from app.routers.suppliers import entity_events
from app.storage import StorageError, get_storage
from app.suppliers import schemas as sc
from app.suppliers import service as supplier_svc

router = APIRouter(tags=["rfqs"])
READ, MANAGE, APPROVE = Permission.RFQS_READ, Permission.RFQS_MANAGE, Permission.RFQS_APPROVE
QUOTES, CMP_READ, CMP_RUN = Permission.QUOTES_MANAGE, Permission.COMPARISONS_READ, Permission.COMPARISONS_RUN
STATUSES = "^(draft|pending_approval|approved|sent|closed|awarded|cancelled)$"


def _filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:120]


def _download(data: bytes, media_type: str, filename: str) -> Response:
    return Response(data, media_type=media_type, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{urlquote(_filename(filename))}",
        "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox",
    })


async def rfq_out(db: AsyncSession, rfq: Rfq) -> rs.RfqOut:
    await set_tenant(db, rfq.organization_id)  # services commit, which ends the tenant-scoped transaction
    items = await svc.items_of(db, rfq)
    pairs = await svc.invited(db, rfq)
    states = await supplier_svc.load_state(db, rfq.organization_id, [s.id for _, s in pairs])
    contacts = dict((await db.execute(
        select(SupplierContact.supplier_id, func.count()).where(SupplierContact.supplier_id.in_([s.id for _, s in pairs]))
        .group_by(SupplierContact.supplier_id)
    )).all()) if pairs else {}
    category = await db.scalar(select(SupplierCategory.name).where(SupplierCategory.id == rfq.category_id)) if rfq.category_id else None
    return rs.RfqOut(
        id=rfq.id, number=rfq.number, title=rfq.title, status=rfq.status, version=rfq.version, currency=rfq.currency,
        response_due_date=rfq.response_due_date, message=rfq.message, category_id=rfq.category_id, category_name=category,
        source_run_id=rfq.source_run_id, content_sha256=rfq.content_sha256, created_by=rfq.created_by,
        submitted_by=rfq.submitted_by, submitted_at=rfq.submitted_at, approved_by=rfq.approved_by,
        approved_at=rfq.approved_at, sent_at=rfq.sent_at, closed_at=rfq.closed_at, decision_note=rfq.decision_note,
        awarded_supplier_id=rfq.awarded_supplier_id, award_comparison_id=rfq.award_comparison_id,
        awarded_by=rfq.awarded_by, awarded_at=rfq.awarded_at, award_note=rfq.award_note, created_at=rfq.created_at,
        updated_at=rfq.updated_at,
        items=[rs.ItemOut(id=i.id, position=i.position, line=n, kind=i.kind, statement=i.statement, category=i.category,
                          priority=i.priority, quantity=i.quantity, unit=i.unit, parameters=i.parameters,
                          source_requirement_id=i.source_requirement_id) for n, i in enumerate(items, start=1)],
        suppliers=[rs.RfqSupplierOut(supplier_id=s.id, code=s.code, name=s.name, supplier_status=s.status,
                                     qualified=states[s.id].summary(s).qualified, contact_count=contacts.get(s.id, 0),
                                     status=link.status, sent_at=link.sent_at) for link, s in pairs],
        readiness=await svc.readiness(db, rfq) if rfq.status == "draft" else [],
    )


# --- RFQs ---------------------------------------------------------------------------------------------------------------
@router.get("/rfqs", response_model=rs.RfqListOut)
async def list_rfqs(
    status: str | None = Query(None, pattern=STATUSES), q: str | None = Query(None, max_length=100),
    limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0),
    p: Principal = Depends(require_permission(READ)), db: AsyncSession = Depends(get_db),
):
    conds = [Rfq.organization_id == p.org_id]
    if status:
        conds.append(Rfq.status == status)
    if q:
        pattern = "%" + re.sub(r"([\\%_])", r"\\\1", q.strip()) + "%"
        conds.append(Rfq.title.ilike(pattern, escape="\\") | Rfq.number.ilike(pattern, escape="\\"))
    total = await db.scalar(select(func.count()).select_from(Rfq).where(*conds)) or 0
    rows = list(await db.scalars(select(Rfq).where(*conds).order_by(Rfq.created_at.desc(), Rfq.id).limit(limit).offset(offset)))
    ids = [r.id for r in rows]
    sup = dict((await db.execute(select(RfqSupplier.rfq_id, func.count()).where(RfqSupplier.rfq_id.in_(ids)).group_by(RfqSupplier.rfq_id))).all()) if ids else {}
    quo = dict((await db.execute(select(RfqQuote.rfq_id, func.count()).where(RfqQuote.rfq_id.in_(ids)).group_by(RfqQuote.rfq_id))).all()) if ids else {}
    return rs.RfqListOut(total=total, items=[rs.RfqListItem(
        id=r.id, number=r.number, title=r.title, status=r.status, version=r.version, currency=r.currency,
        response_due_date=r.response_due_date, supplier_count=sup.get(r.id, 0), quote_count=quo.get(r.id, 0),
        created_at=r.created_at, updated_at=r.updated_at) for r in rows])


@router.post("/rfqs", response_model=rs.RfqOut, status_code=201)
async def create_rfq(body: rs.RfqCreate, p: Principal = Depends(require_permission(MANAGE)), db: AsyncSession = Depends(get_db)):
    return await rfq_out(db, await svc.create_rfq(db, p, body))


@router.get("/rfqs/{rfq_id}", response_model=rs.RfqOut)
async def get_rfq(rfq_id: uuid.UUID, p: Principal = Depends(require_permission(READ)), db: AsyncSession = Depends(get_db)):
    return await rfq_out(db, await svc.get_rfq(db, p, rfq_id))


@router.patch("/rfqs/{rfq_id}", response_model=rs.RfqOut)
async def update_rfq(rfq_id: uuid.UUID, body: rs.RfqUpdate, p: Principal = Depends(require_permission(MANAGE)), db: AsyncSession = Depends(get_db)):
    return await rfq_out(db, await svc.update_rfq(db, p, await svc.get_rfq(db, p, rfq_id, lock=True), body))


@router.post("/rfqs/{rfq_id}/items", response_model=rs.RfqOut, status_code=201)
async def add_item(rfq_id: uuid.UUID, body: rs.ItemIn, p: Principal = Depends(require_permission(MANAGE)), db: AsyncSession = Depends(get_db)):
    rfq = await svc.get_rfq(db, p, rfq_id, lock=True)
    await svc.add_item(db, p, rfq, body)
    return await rfq_out(db, rfq)


@router.patch("/rfqs/{rfq_id}/items/{item_id}", response_model=rs.RfqOut)
async def update_item(rfq_id: uuid.UUID, item_id: uuid.UUID, body: rs.ItemUpdate, p: Principal = Depends(require_permission(MANAGE)), db: AsyncSession = Depends(get_db)):
    rfq = await svc.get_rfq(db, p, rfq_id, lock=True)
    await svc.update_item(db, p, rfq, item_id, body)
    return await rfq_out(db, rfq)


@router.delete("/rfqs/{rfq_id}/items/{item_id}", response_model=rs.RfqOut)
async def delete_item(rfq_id: uuid.UUID, item_id: uuid.UUID, p: Principal = Depends(require_permission(MANAGE)), db: AsyncSession = Depends(get_db)):
    rfq = await svc.get_rfq(db, p, rfq_id, lock=True)
    await svc.delete_item(db, p, rfq, item_id)
    return await rfq_out(db, rfq)


@router.post("/rfqs/{rfq_id}/suppliers", response_model=rs.RfqOut)
async def add_suppliers(rfq_id: uuid.UUID, body: rs.SupplierIdsIn, p: Principal = Depends(require_permission(MANAGE)), db: AsyncSession = Depends(get_db)):
    rfq = await svc.get_rfq(db, p, rfq_id, lock=True)
    await svc.add_suppliers(db, p, rfq, body.supplier_ids)
    return await rfq_out(db, rfq)


@router.delete("/rfqs/{rfq_id}/suppliers/{supplier_id}", response_model=rs.RfqOut)
async def remove_supplier(rfq_id: uuid.UUID, supplier_id: uuid.UUID, p: Principal = Depends(require_permission(MANAGE)), db: AsyncSession = Depends(get_db)):
    rfq = await svc.get_rfq(db, p, rfq_id, lock=True)
    await svc.remove_supplier(db, p, rfq, supplier_id)
    return await rfq_out(db, rfq)


@router.get("/rfqs/{rfq_id}/supplier-suggestions", response_model=list[rs.SuggestionOut])
async def suggestions(rfq_id: uuid.UUID, p: Principal = Depends(require_permission(READ)), db: AsyncSession = Depends(get_db)):
    return await svc.suggest_suppliers(db, p, await svc.get_rfq(db, p, rfq_id))


@router.post("/rfqs/{rfq_id}/submit", response_model=rs.RfqOut)
async def submit(rfq_id: uuid.UUID, p: Principal = Depends(require_permission(MANAGE)), db: AsyncSession = Depends(get_db)):
    return await rfq_out(db, await svc.submit(db, p, await svc.get_rfq(db, p, rfq_id, lock=True)))


@router.post("/rfqs/{rfq_id}/approve", response_model=rs.RfqOut)
async def approve(rfq_id: uuid.UUID, body: sc.NoteIn, p: Principal = Depends(require_permission(APPROVE)), db: AsyncSession = Depends(get_db)):
    return await rfq_out(db, await svc.approve(db, p, await svc.get_rfq(db, p, rfq_id, lock=True), body.note))


@router.post("/rfqs/{rfq_id}/return", response_model=rs.RfqOut)
async def return_to_draft(rfq_id: uuid.UUID, body: sc.ReasonIn, p: Principal = Depends(require_permission(APPROVE)), db: AsyncSession = Depends(get_db)):
    return await rfq_out(db, await svc.return_to_draft(db, p, await svc.get_rfq(db, p, rfq_id, lock=True), body.reason))


@router.post("/rfqs/{rfq_id}/send", response_model=rs.RfqOut)
async def send(rfq_id: uuid.UUID, p: Principal = Depends(require_permission(MANAGE)), db: AsyncSession = Depends(get_db)):
    return await rfq_out(db, await svc.send(db, p, await svc.get_rfq(db, p, rfq_id, lock=True)))


@router.post("/rfqs/{rfq_id}/close", response_model=rs.RfqOut)
async def close(rfq_id: uuid.UUID, body: sc.NoteIn, p: Principal = Depends(require_permission(MANAGE)), db: AsyncSession = Depends(get_db)):
    return await rfq_out(db, await svc.close(db, p, await svc.get_rfq(db, p, rfq_id, lock=True), body.note))


@router.post("/rfqs/{rfq_id}/cancel", response_model=rs.RfqOut)
async def cancel(rfq_id: uuid.UUID, body: sc.ReasonIn, p: Principal = Depends(require_permission(MANAGE)), db: AsyncSession = Depends(get_db)):
    return await rfq_out(db, await svc.cancel(db, p, await svc.get_rfq(db, p, rfq_id, lock=True), body.reason))


@router.post("/rfqs/{rfq_id}/award", response_model=rs.RfqOut)
async def award(rfq_id: uuid.UUID, body: rs.AwardIn, p: Principal = Depends(require_permission(APPROVE)), db: AsyncSession = Depends(get_db)):
    return await rfq_out(db, await svc.award(db, p, await svc.get_rfq(db, p, rfq_id, lock=True), body))


@router.get("/rfqs/{rfq_id}/events", response_model=list[sc.EventOut])
async def rfq_events(rfq_id: uuid.UUID, p: Principal = Depends(require_permission(READ)), db: AsyncSession = Depends(get_db)):
    rfq = await svc.get_rfq(db, p, rfq_id)
    return await entity_events(db, p.org_id, "rfq", rfq.id)


# --- Exports ------------------------------------------------------------------------------------------------------------------
@router.get("/rfqs/{rfq_id}/pdf")
async def rfq_pdf(rfq_id: uuid.UUID, p: Principal = Depends(require_permission(READ)), db: AsyncSession = Depends(get_db)):
    rfq = await svc.get_rfq(db, p, rfq_id)
    document = await svc.document_for(db, rfq)
    data = render.render_rfq_pdf(document, status=rfq.status, content_hash=rfq.content_sha256,
                                 issued=(rfq.approved_at.date() if rfq.approved_at else None))
    record(db, org_id=p.org_id, user_id=p.user.id, action="rfq.exported", ip=p.ip, meta={"rfq_id": str(rfq.id), "format": "pdf", "version": rfq.version})
    await db.commit()
    return _download(data, "application/pdf", f"{rfq.number}-v{rfq.version}.pdf")


@router.get("/rfqs/{rfq_id}/export.json")
async def rfq_json(rfq_id: uuid.UUID, p: Principal = Depends(require_permission(READ)), db: AsyncSession = Depends(get_db)):
    rfq = await svc.get_rfq(db, p, rfq_id)
    document = await svc.document_for(db, rfq)
    pairs = await svc.invited(db, rfq)
    record(db, org_id=p.org_id, user_id=p.user.id, action="rfq.exported", ip=p.ip, meta={"rfq_id": str(rfq.id), "format": "json"})
    await db.commit()
    payload = {"rfq": document, "status": rfq.status, "content_sha256": rfq.content_sha256,
               "approved_at": rfq.approved_at.isoformat() if rfq.approved_at else None,
               "suppliers": [{"code": s.code, "name": s.name, "status": link.status} for link, s in pairs]}
    return _download(json.dumps(payload, indent=2, ensure_ascii=False).encode(), "application/json", f"{rfq.number}-v{rfq.version}.json")


# --- Packages -------------------------------------------------------------------------------------------------------------------
async def _packages_out(db: AsyncSession, rfq: Rfq) -> list[rs.PackageOut]:
    await set_tenant(db, rfq.organization_id)
    rows = (await db.execute(
        select(RfqPackage, Supplier.name).join(Supplier, Supplier.id == RfqPackage.supplier_id)
        .where(RfqPackage.rfq_id == rfq.id).order_by(RfqPackage.rfq_version.desc(), Supplier.name)
    )).all()
    return [rs.PackageOut(id=pk.id, supplier_id=pk.supplier_id, supplier_name=name, rfq_version=pk.rfq_version,
                          subject=pk.subject, to_addresses=pk.to_addresses, sha256=pk.sha256, size_bytes=pk.size_bytes,
                          manifest=pk.manifest, send_status=pk.send_status, sent_at=pk.sent_at, send_error=pk.send_error,
                          created_at=pk.created_at) for pk, name in rows]


@router.get("/rfqs/{rfq_id}/packages", response_model=list[rs.PackageOut])
async def list_packages(rfq_id: uuid.UUID, p: Principal = Depends(require_permission(READ)), db: AsyncSession = Depends(get_db)):
    return await _packages_out(db, await svc.get_rfq(db, p, rfq_id))


@router.post("/rfqs/{rfq_id}/packages", response_model=list[rs.PackageOut])
async def generate_packages(rfq_id: uuid.UUID, p: Principal = Depends(require_permission(MANAGE)), db: AsyncSession = Depends(get_db)):
    rfq = await svc.get_rfq(db, p, rfq_id, lock=True)
    await svc.generate_packages(db, p, rfq)
    return await _packages_out(db, rfq)


@router.post("/rfqs/{rfq_id}/packages/{package_id}/resend", response_model=list[rs.PackageOut])
async def resend_package(rfq_id: uuid.UUID, package_id: uuid.UUID, p: Principal = Depends(require_permission(MANAGE)), db: AsyncSession = Depends(get_db)):
    rfq = await svc.get_rfq(db, p, rfq_id, lock=True)
    await svc.resend_package(db, p, rfq, package_id)
    return await _packages_out(db, rfq)


@router.get("/rfqs/{rfq_id}/packages/{package_id}/download")
async def download_package(rfq_id: uuid.UUID, package_id: uuid.UUID, p: Principal = Depends(require_permission(READ)), db: AsyncSession = Depends(get_db)):
    rfq = await svc.get_rfq(db, p, rfq_id)
    pkg = await db.scalar(select(RfqPackage).where(RfqPackage.id == package_id, RfqPackage.rfq_id == rfq.id))
    if pkg is None:
        raise not_found("Package not found")
    try:
        data = await get_storage().get(pkg.storage_backend, pkg.storage_key)
    except StorageError:
        raise ApiError(503, "storage_unavailable", "File storage is unavailable. Try again shortly.") from None
    record(db, org_id=p.org_id, user_id=p.user.id, action="rfq.package_downloaded", ip=p.ip, meta={"package_id": str(pkg.id)})
    await db.commit()
    return _download(data, "message/rfc822", f"{rfq.number}-v{pkg.rfq_version}-{pkg.supplier_id}.eml")


# --- Quotes -----------------------------------------------------------------------------------------------------------------------
async def _quotes_out(db: AsyncSession, org_id: uuid.UUID, rfq_id: uuid.UUID, quote_id: uuid.UUID | None = None) -> list[rs.QuoteOut]:
    await set_tenant(db, org_id)
    stmt = select(RfqQuote, Supplier.name).join(Supplier, Supplier.id == RfqQuote.supplier_id).where(RfqQuote.rfq_id == rfq_id)
    if quote_id:
        stmt = stmt.where(RfqQuote.id == quote_id)
    rows = (await db.execute(stmt.order_by(Supplier.name))).all()
    lines: dict[uuid.UUID, list[RfqQuoteLine]] = {q.id: [] for q, _ in rows}
    if rows:
        for ln in await db.scalars(select(RfqQuoteLine).where(RfqQuoteLine.quote_id.in_(lines)).order_by(RfqQuoteLine.position)):
            lines[ln.quote_id].append(ln)
    return [rs.QuoteOut(
        id=q.id, supplier_id=q.supplier_id, supplier_name=name, document_id=q.document_id, source=q.source, status=q.status,
        currency=q.currency, incoterms=q.incoterms, payment_terms=q.payment_terms, valid_until=q.valid_until, notes=q.notes,
        verified_by=q.verified_by, verified_at=q.verified_at, created_at=q.created_at,
        lines=[rs.QuoteLineOut(id=ln.id, position=ln.position, rfq_item_id=ln.rfq_item_id, match_status=ln.match_status,
                               match_score=ln.match_score, description=ln.description, quantity=ln.quantity, unit=ln.unit,
                               unit_price=ln.unit_price, total_price=ln.total_price, lead_time_days=ln.lead_time_days,
                               confidence=ln.confidence, source_quote=ln.source_quote) for ln in lines[q.id]],
    ) for q, name in rows]


async def _one_quote(db: AsyncSession, quote: RfqQuote) -> rs.QuoteOut:
    return (await _quotes_out(db, quote.organization_id, quote.rfq_id, quote.id))[0]


@router.get("/rfqs/{rfq_id}/quotes", response_model=list[rs.QuoteOut])
async def list_quotes(rfq_id: uuid.UUID, p: Principal = Depends(require_permission(READ)), db: AsyncSession = Depends(get_db)):
    rfq = await svc.get_rfq(db, p, rfq_id)
    return await _quotes_out(db, p.org_id, rfq.id)


@router.post("/rfqs/{rfq_id}/quotes", response_model=rs.QuoteOut, status_code=201)
async def create_quote(rfq_id: uuid.UUID, body: rs.QuoteIn, p: Principal = Depends(require_permission(QUOTES)), db: AsyncSession = Depends(get_db)):
    rfq = await svc.get_rfq(db, p, rfq_id, lock=True)
    return await _one_quote(db, await svc.create_quote(db, p, rfq, body))


@router.post("/rfqs/{rfq_id}/quotes/from-document", response_model=rs.QuoteOut, status_code=201)
async def quote_from_document(rfq_id: uuid.UUID, body: rs.QuoteFromDocumentIn, p: Principal = Depends(require_permission(QUOTES)), db: AsyncSession = Depends(get_db)):
    rfq = await svc.get_rfq(db, p, rfq_id, lock=True)
    return await _one_quote(db, await svc.quote_from_document(db, p, rfq, body))


@router.patch("/quotes/{quote_id}", response_model=rs.QuoteOut)
async def update_quote(quote_id: uuid.UUID, body: rs.QuoteHeader, p: Principal = Depends(require_permission(QUOTES)), db: AsyncSession = Depends(get_db)):
    return await _one_quote(db, await svc.update_quote(db, p, quote_id, body))


@router.put("/quotes/{quote_id}/lines", response_model=rs.QuoteOut)
async def replace_lines(quote_id: uuid.UUID, body: rs.QuoteLinesIn, p: Principal = Depends(require_permission(QUOTES)), db: AsyncSession = Depends(get_db)):
    return await _one_quote(db, await svc.replace_lines(db, p, quote_id, body))


@router.post("/quotes/{quote_id}/lines/{line_id}/match", response_model=rs.QuoteOut)
async def match_line(quote_id: uuid.UUID, line_id: uuid.UUID, body: rs.MatchIn, p: Principal = Depends(require_permission(QUOTES)), db: AsyncSession = Depends(get_db)):
    return await _one_quote(db, await svc.set_match(db, p, quote_id, line_id, body.rfq_item_id))


@router.post("/quotes/{quote_id}/verify", response_model=rs.QuoteOut)
async def verify_quote(quote_id: uuid.UUID, p: Principal = Depends(require_permission(QUOTES)), db: AsyncSession = Depends(get_db)):
    return await _one_quote(db, await svc.verify_quote(db, p, quote_id))


@router.post("/quotes/{quote_id}/reopen", response_model=rs.QuoteOut)
async def reopen_quote(quote_id: uuid.UUID, body: sc.ReasonIn, p: Principal = Depends(require_permission(QUOTES)), db: AsyncSession = Depends(get_db)):
    return await _one_quote(db, await svc.reopen_quote(db, p, quote_id, body.reason))


# --- Comparisons ----------------------------------------------------------------------------------------------------------------------
async def _comparison(db: AsyncSession, p: Principal, comparison_id: uuid.UUID) -> tuple[Comparison, Rfq]:
    row = await db.scalar(select(Comparison).where(Comparison.id == comparison_id, Comparison.organization_id == p.org_id))
    if row is None:
        raise not_found("Comparison not found")
    rfq = await db.scalar(select(Rfq).where(Rfq.id == row.rfq_id))
    assert rfq is not None
    return row, rfq


async def _comparison_out(db: AsyncSession, row: Comparison, rfq: Rfq) -> rs.ComparisonOut:
    await set_tenant(db, rfq.organization_id)
    return rs.ComparisonOut(id=row.id, created_at=row.created_at, created_by=row.created_by, params=row.params,
                            inputs_sha256=row.inputs_sha256, stale=await svc.is_stale(db, rfq, row), result=row.result)


@router.post("/rfqs/{rfq_id}/comparisons", response_model=rs.ComparisonOut, status_code=201)
async def run_comparison(rfq_id: uuid.UUID, body: rs.CompareIn, p: Principal = Depends(require_permission(CMP_RUN)), db: AsyncSession = Depends(get_db)):
    rfq = await svc.get_rfq(db, p, rfq_id)
    row = await svc.run_comparison(db, p, rfq, body.weights)
    return await _comparison_out(db, row, rfq)


@router.get("/rfqs/{rfq_id}/comparisons", response_model=list[rs.ComparisonBrief])
async def list_comparisons(rfq_id: uuid.UUID, p: Principal = Depends(require_permission(CMP_READ)), db: AsyncSession = Depends(get_db)):
    rfq = await svc.get_rfq(db, p, rfq_id)
    rows = list(await db.scalars(select(Comparison).where(Comparison.rfq_id == rfq.id).order_by(Comparison.created_at.desc()).limit(50)))
    current = await svc.comparison_inputs(db, rfq)
    out = []
    for row in rows:
        snapshot = dict(current, as_of=row.result.get("as_of", current["as_of"]))
        best = row.result.get("highest_score_supplier_id")
        out.append(rs.ComparisonBrief(id=row.id, created_at=row.created_at, created_by=row.created_by,
                                      weights=row.params.get("weights", {}), highest_score_supplier_id=uuid.UUID(best) if best else None,
                                      stale=cmp.inputs_hash(snapshot) != row.inputs_sha256))
    return out


@router.get("/comparisons/{comparison_id}", response_model=rs.ComparisonOut)
async def get_comparison(comparison_id: uuid.UUID, p: Principal = Depends(require_permission(CMP_READ)), db: AsyncSession = Depends(get_db)):
    row, rfq = await _comparison(db, p, comparison_id)
    return await _comparison_out(db, row, rfq)


async def _award_info(db: AsyncSession, rfq: Rfq, row: Comparison) -> dict | None:
    if rfq.award_comparison_id != row.id or not rfq.awarded_supplier_id:
        return None
    supplier = await db.scalar(select(Supplier.name).where(Supplier.id == rfq.awarded_supplier_id))
    by = await db.scalar(select(User.email).where(User.id == rfq.awarded_by))
    return {"supplier": supplier, "by": by, "at": rfq.awarded_at.date().isoformat() if rfq.awarded_at else "", "note": rfq.award_note}


@router.get("/comparisons/{comparison_id}/pdf")
async def comparison_pdf(comparison_id: uuid.UUID, p: Principal = Depends(require_permission(CMP_READ)), db: AsyncSession = Depends(get_db)):
    row, rfq = await _comparison(db, p, comparison_id)
    data = render.render_comparison_pdf(
        row.result, rfq_number=rfq.number, rfq_title=rfq.title, organization=await svc.organization_name(db, rfq.organization_id),
        generated_at=row.created_at.strftime("%Y-%m-%d %H:%M UTC"), award=await _award_info(db, rfq, row),
    )
    record(db, org_id=p.org_id, user_id=p.user.id, action="comparison.exported", ip=p.ip, meta={"comparison_id": str(row.id), "format": "pdf"})
    await db.commit()
    return _download(data, "application/pdf", f"{rfq.number}-comparison.pdf")


@router.get("/comparisons/{comparison_id}/xlsx")
async def comparison_xlsx(comparison_id: uuid.UUID, p: Principal = Depends(require_permission(CMP_READ)), db: AsyncSession = Depends(get_db)):
    row, rfq = await _comparison(db, p, comparison_id)
    data = xlsx.comparison_workbook(row.result, rfq_number=rfq.number, rfq_title=rfq.title,
                                    generated_at=row.created_at.strftime("%Y-%m-%d %H:%M UTC"))
    record(db, org_id=p.org_id, user_id=p.user.id, action="comparison.exported", ip=p.ip, meta={"comparison_id": str(row.id), "format": "xlsx"})
    await db.commit()
    return _download(data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", f"{rfq.number}-comparison.xlsx")

