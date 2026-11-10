"""RFQ lifecycle: generation from approved requirements, approval, supplier packages, quotes, comparison, award."""
import hashlib
import re
import uuid
from datetime import UTC, date, datetime
from email.utils import parseaddr
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.deps import Principal
from app.errors import ApiError, bad_request, conflict, forbidden, not_found
from app.events import emit_event, next_number
from app.jobs import queue
from app.models import (
    Comparison,
    Document,
    DocumentExtraction,
    ExtractionRun,
    Organization,
    Parameter,
    Requirement,
    Rfq,
    RfqItem,
    RfqPackage,
    RfqQuote,
    RfqQuoteLine,
    RfqSupplier,
    Supplier,
    SupplierCategory,
    SupplierCategoryLink,
    SupplierContact,
)
from app.rfqs import comparison as cmp
from app.rfqs import content, matching, package, render
from app.rfqs import schemas as rs
from app.storage import StorageError, get_storage
from app.suppliers import service as supplier_svc

settings = get_settings()
EDITABLE = ("draft",)
SEND_JOB = "rfq.send_package"


def utcnow() -> datetime:
    return datetime.now(UTC)


def event(db: AsyncSession, p: Principal | None, rfq: Rfq, action: str, *, from_state: str | None = None,
          to_state: str | None = None, data: dict[str, Any] | None = None, note: str | None = None) -> None:
    emit_event(db, org_id=rfq.organization_id, entity_type="rfq", entity_id=rfq.id, action=action,
               actor=p.user.id if p else None, from_state=from_state, to_state=to_state, data=data, note=note,
               ip=p.ip if p else None)


# --- Loading ---------------------------------------------------------------------------------------------------------
async def get_rfq(db: AsyncSession, p: Principal, rfq_id: uuid.UUID, *, lock: bool = False) -> Rfq:
    stmt = select(Rfq).where(Rfq.id == rfq_id, Rfq.organization_id == p.org_id)
    rfq = await db.scalar(stmt.with_for_update() if lock else stmt)
    if rfq is None:
        raise not_found("RFQ not found")
    return rfq


async def items_of(db: AsyncSession, rfq: Rfq) -> list[RfqItem]:
    return list(await db.scalars(select(RfqItem).where(RfqItem.rfq_id == rfq.id).order_by(RfqItem.position, RfqItem.created_at)))


async def invited(db: AsyncSession, rfq: Rfq) -> list[tuple[RfqSupplier, Supplier]]:
    rows = (await db.execute(
        select(RfqSupplier, Supplier).join(Supplier, Supplier.id == RfqSupplier.supplier_id)
        .where(RfqSupplier.rfq_id == rfq.id).order_by(Supplier.name)
    )).all()
    return [(rs_, s) for rs_, s in rows]


def require_draft(rfq: Rfq) -> None:
    if rfq.status not in EDITABLE:
        raise conflict("rfq_locked", f"The RFQ is {rfq.status.replace('_', ' ')}; return it to draft to change it")


# --- Creation -------------------------------------------------------------------------------------------------------
def _param_from_extraction(prm: Parameter) -> dict[str, Any]:
    if prm.unknown:
        return {"name": prm.name, "value": None, "unit": None, "qualifier": None, "unknown": True}
    value: Any = prm.value_number if prm.value_type == "number" else prm.value_text
    return {"name": prm.name, "value": value, "unit": prm.unit, "qualifier": prm.qualifier, "unknown": False}


async def items_from_run(db: AsyncSession, run: ExtractionRun) -> list[dict[str, Any]]:
    """Approved requirements become RFQ lines. Rejected items are left out; unknowns become questions to suppliers."""
    reqs = list(await db.scalars(
        select(Requirement).where(Requirement.run_id == run.id, Requirement.validation_state != "rejected")
        .order_by(Requirement.position)
    ))
    params = list(await db.scalars(
        select(Parameter).where(Parameter.run_id == run.id, Parameter.validation_state != "rejected")
        .order_by(Parameter.position)
    ))
    by_req: dict[uuid.UUID | None, list[Parameter]] = {}
    for prm in params:
        by_req.setdefault(prm.requirement_id, []).append(prm)
    items: list[dict[str, Any]] = []
    for req in reqs:
        own = by_req.get(req.id, [])
        qty = next((x for x in own if x.name == "quantity" and not x.unknown and x.value_number), None)
        items.append({
            "kind": "requirement", "statement": req.statement, "category": req.category, "priority": req.priority,
            "quantity": qty.value_number if qty else None, "unit": qty.unit if qty else None,
            "parameters": [_param_from_extraction(x) for x in own if x is not qty],
            "source_requirement_id": req.id,
        })
    for prm in by_req.get(None, []):
        if prm.unknown:
            items.append({
                "kind": "requirement", "statement": f"Supplier to state: {prm.name.replace('_', ' ')}",
                "category": "other", "priority": "unknown", "quantity": None, "unit": None,
                "parameters": [_param_from_extraction(prm)], "source_requirement_id": None,
            })
    return items


async def _check_category(db: AsyncSession, org_id: uuid.UUID, category_id: uuid.UUID | None) -> None:
    if category_id and not await db.scalar(
        select(SupplierCategory.id).where(SupplierCategory.id == category_id, SupplierCategory.organization_id == org_id)
    ):
        raise bad_request("invalid_category", "The category does not exist")


async def create_rfq(db: AsyncSession, p: Principal, body: rs.RfqCreate) -> Rfq:
    await _check_category(db, p.org_id, body.category_id)
    item_rows: list[dict[str, Any]] = []
    if body.from_run_id:
        run = await db.scalar(select(ExtractionRun).where(
            ExtractionRun.id == body.from_run_id, ExtractionRun.organization_id == p.org_id
        ))
        if run is None:
            raise not_found("Extraction run not found")
        if run.status != "approved":
            raise conflict("run_not_approved", "Only approved requirement extractions can be turned into an RFQ")
        item_rows = await items_from_run(db, run)
    year = utcnow().year
    number = await next_number(db, p.org_id, f"rfq-{year}")
    rfq = Rfq(
        organization_id=p.org_id, number=f"{settings.rfq_number_prefix}-{year}-{number:04d}", title=body.title.strip(),
        source_run_id=body.from_run_id, category_id=body.category_id,
        currency=(body.currency or settings.default_currency).upper(), response_due_date=body.response_due_date,
        message=body.message, created_by=p.user.id,
    )
    db.add(rfq)
    await db.flush()
    for position, row in enumerate(item_rows):
        db.add(RfqItem(organization_id=p.org_id, rfq_id=rfq.id, position=position, **row))
    event(db, p, rfq, "created", to_state="draft", data={"number": rfq.number, "from_run_id": str(body.from_run_id) if body.from_run_id else None, "items": len(item_rows)})
    await db.refresh(rfq)
    await db.commit()
    return rfq


async def update_rfq(db: AsyncSession, p: Principal, rfq: Rfq, body: rs.RfqUpdate) -> Rfq:
    require_draft(rfq)
    data = body.model_dump(exclude_unset=True)
    if "category_id" in data:
        await _check_category(db, p.org_id, data["category_id"])
    changes = {}
    for key, value in data.items():
        if key in ("title", "currency") and value is None:
            continue
        if getattr(rfq, key) != value:
            changes[key] = {"from": str(getattr(rfq, key)), "to": str(value)}
            setattr(rfq, key, value.strip() if key == "title" else value)
    if changes:
        rfq.updated_at = utcnow()
        event(db, p, rfq, "updated", data=changes)
    await db.commit()
    return rfq


async def _renumber(db: AsyncSession, rfq: Rfq) -> None:
    for n, item in enumerate(await items_of(db, rfq)):
        item.position = n


async def add_item(db: AsyncSession, p: Principal, rfq: Rfq, body: rs.ItemIn) -> RfqItem:
    require_draft(rfq)
    position = await db.scalar(select(func.count()).select_from(RfqItem).where(RfqItem.rfq_id == rfq.id)) or 0
    item = RfqItem(
        organization_id=p.org_id, rfq_id=rfq.id, position=position, kind="manual", statement=body.statement.strip(),
        category=body.category, priority=body.priority, quantity=body.quantity, unit=body.unit,
        parameters=[x.model_dump() for x in body.parameters],
    )
    db.add(item)
    await db.flush()
    rfq.updated_at = utcnow()
    event(db, p, rfq, "item_added", data={"item_id": str(item.id), "statement": item.statement[:200]})
    await db.refresh(item)
    await db.commit()
    return item


async def _item(db: AsyncSession, rfq: Rfq, item_id: uuid.UUID) -> RfqItem:
    item = await db.scalar(select(RfqItem).where(RfqItem.id == item_id, RfqItem.rfq_id == rfq.id))
    if item is None:
        raise not_found("RFQ line not found")
    return item


async def update_item(db: AsyncSession, p: Principal, rfq: Rfq, item_id: uuid.UUID, body: rs.ItemUpdate) -> RfqItem:
    require_draft(rfq)
    item = await _item(db, rfq, item_id)
    data = body.model_dump(exclude_unset=True)
    position = data.pop("position", None)
    for key, value in data.items():
        if key == "parameters":
            value = [x if isinstance(x, dict) else x.model_dump() for x in (value or [])]
        if key == "statement" and value is not None:
            value = value.strip()
        if key in ("statement", "category", "priority") and value is None:
            continue
        setattr(item, key, value)
    if position is not None:
        items = [i for i in await items_of(db, rfq) if i.id != item.id]
        items.insert(min(position, len(items)), item)
        for n, it in enumerate(items):
            it.position = n
    rfq.updated_at = utcnow()
    event(db, p, rfq, "item_updated", data={"item_id": str(item.id), "fields": sorted(data) + (["position"] if position is not None else [])})
    await db.commit()
    return item


async def delete_item(db: AsyncSession, p: Principal, rfq: Rfq, item_id: uuid.UUID) -> None:
    require_draft(rfq)
    item = await _item(db, rfq, item_id)
    await db.execute(delete(RfqItem).where(RfqItem.id == item.id))
    await _renumber(db, rfq)
    rfq.updated_at = utcnow()
    event(db, p, rfq, "item_removed", data={"item_id": str(item.id), "statement": item.statement[:200]})
    await db.commit()


# --- Suppliers on the RFQ ---------------------------------------------------------------------------------------------
async def add_suppliers(db: AsyncSession, p: Principal, rfq: Rfq, ids: list[uuid.UUID]) -> None:
    require_draft(rfq)
    ids = list(dict.fromkeys(ids))
    suppliers = list(await db.scalars(select(Supplier).where(Supplier.id.in_(ids), Supplier.organization_id == p.org_id)))
    if len(suppliers) != len(ids):
        raise bad_request("invalid_supplier", "One or more suppliers do not exist")
    blocked = [s.name for s in suppliers if s.status in ("blocked", "disqualified")]
    if blocked:
        raise conflict("supplier_not_eligible", "Blocked or disqualified suppliers cannot be invited: " + ", ".join(blocked))
    existing = set(await db.scalars(select(RfqSupplier.supplier_id).where(RfqSupplier.rfq_id == rfq.id)))
    added = [s for s in suppliers if s.id not in existing]
    for s in added:
        db.add(RfqSupplier(organization_id=p.org_id, rfq_id=rfq.id, supplier_id=s.id))
    if added:
        rfq.updated_at = utcnow()
        event(db, p, rfq, "suppliers_added", data={"suppliers": [s.code for s in added]})
    await db.commit()


async def remove_supplier(db: AsyncSession, p: Principal, rfq: Rfq, supplier_id: uuid.UUID) -> None:
    require_draft(rfq)
    row = await db.scalar(select(RfqSupplier).where(RfqSupplier.rfq_id == rfq.id, RfqSupplier.supplier_id == supplier_id))
    if row is None:
        raise not_found("This supplier is not on the RFQ")
    await db.execute(delete(RfqSupplier).where(RfqSupplier.id == row.id))
    rfq.updated_at = utcnow()
    event(db, p, rfq, "supplier_removed", data={"supplier_id": str(supplier_id)})
    await db.commit()


async def suggest_suppliers(db: AsyncSession, p: Principal, rfq: Rfq) -> list[rs.SuggestionOut]:
    """Qualified suppliers in the RFQ's category (and its sub-categories) who are not yet invited."""
    conds = [Supplier.organization_id == p.org_id, Supplier.status == "qualified",
             Supplier.id.not_in(select(RfqSupplier.supplier_id).where(RfqSupplier.rfq_id == rfq.id))]
    if rfq.category_id:
        cats = {rfq.category_id}
        all_cats = list(await db.scalars(select(SupplierCategory).where(SupplierCategory.organization_id == p.org_id)))
        grew = True
        while grew:
            grew = False
            for c in all_cats:
                if c.parent_id in cats and c.id not in cats:
                    cats.add(c.id)
                    grew = True
        conds.append(Supplier.id.in_(select(SupplierCategoryLink.supplier_id).where(SupplierCategoryLink.category_id.in_(cats))))
    suppliers = list(await db.scalars(select(Supplier).where(*conds).order_by(Supplier.name).limit(50)))
    states = await supplier_svc.load_state(db, p.org_id, [s.id for s in suppliers])
    names: dict[uuid.UUID, list[str]] = {s.id: [] for s in suppliers}
    if suppliers:
        for sid, name in (await db.execute(
            select(SupplierCategoryLink.supplier_id, SupplierCategory.name)
            .join(SupplierCategory, SupplierCategory.id == SupplierCategoryLink.category_id)
            .where(SupplierCategoryLink.supplier_id.in_(names))
        )).all():
            names[sid].append(name)
    return sorted(
        [rs.SuggestionOut(supplier_id=s.id, code=s.code, name=s.name, qualification_score=states[s.id].score,
                          categories=sorted(names[s.id]))
         for s in suppliers if states[s.id].summary(s).qualified],
        key=lambda x: (-x.qualification_score, x.name),
    )


# --- Canonical document & readiness ----------------------------------------------------------------------------------
async def organization_name(db: AsyncSession, org_id: uuid.UUID) -> str:
    if settings.org_display_name:
        return settings.org_display_name
    return await db.scalar(select(Organization.name).where(Organization.id == org_id)) or "Buyer"


async def document_for(db: AsyncSession, rfq: Rfq) -> dict[str, Any]:
    category = None
    if rfq.category_id:
        category = await db.scalar(select(SupplierCategory.name).where(SupplierCategory.id == rfq.category_id))
    return content.build_document(
        number=rfq.number, title=rfq.title, version=rfq.version, currency=rfq.currency,
        response_due_date=rfq.response_due_date.isoformat() if rfq.response_due_date else None, message=rfq.message,
        organization=await organization_name(db, rfq.organization_id), category=category,
        items=[{"position": i.position, "statement": i.statement, "category": i.category, "priority": i.priority,
                "quantity": i.quantity, "unit": i.unit, "parameters": i.parameters} for i in await items_of(db, rfq)],
    )


async def current_hash(db: AsyncSession, rfq: Rfq) -> str:
    supplier_ids = [str(s.id) for _, s in await invited(db, rfq)]
    return content.approval_hash(await document_for(db, rfq), supplier_ids)


async def readiness(db: AsyncSession, rfq: Rfq) -> list[str]:
    """Everything that must be true before an RFQ can go out. Empty list means ready."""
    problems: list[str] = []
    if not await items_of(db, rfq):
        problems.append("Add at least one requirement line.")
    if rfq.response_due_date is None:
        problems.append("Set a quotation due date.")
    elif rfq.response_due_date <= utcnow().date():
        problems.append("The quotation due date must be in the future.")
    pairs = await invited(db, rfq)
    if not pairs:
        problems.append("Invite at least one supplier.")
    if pairs:
        states = await supplier_svc.load_state(db, rfq.organization_id, [s.id for _, s in pairs])
        contacts = dict((await db.execute(
            select(SupplierContact.supplier_id, func.count()).where(SupplierContact.supplier_id.in_([s.id for _, s in pairs]))
            .group_by(SupplierContact.supplier_id)
        )).all())
        for _, s in pairs:
            if not states[s.id].summary(s).qualified:
                problems.append(f"{s.name} is not currently qualified.")
            if not contacts.get(s.id):
                problems.append(f"{s.name} has no contact to send the RFQ to.")
    return problems


# --- Approval workflow ---------------------------------------------------------------------------------------------------
async def submit(db: AsyncSession, p: Principal, rfq: Rfq) -> Rfq:
    require_draft(rfq)
    problems = await readiness(db, rfq)
    if problems:
        raise ApiError(409, "not_ready", " ".join(problems))
    rfq.content_sha256 = await current_hash(db, rfq)
    rfq.status, rfq.submitted_by, rfq.submitted_at, rfq.updated_at = "pending_approval", p.user.id, utcnow(), utcnow()
    rfq.decision_note = None
    event(db, p, rfq, "submitted", from_state="draft", to_state="pending_approval", data={"content_sha256": rfq.content_sha256, "version": rfq.version})
    await db.commit()
    return rfq


async def approve(db: AsyncSession, p: Principal, rfq: Rfq, note: str | None) -> Rfq:
    if rfq.status != "pending_approval":
        raise conflict("invalid_state", "Only RFQs pending approval can be approved")
    if settings.require_separate_approver and p.user.id in (rfq.created_by, rfq.submitted_by):
        raise forbidden("The approver must not be the person who created or submitted the RFQ", "segregation_of_duties")
    if await current_hash(db, rfq) != rfq.content_sha256:
        raise conflict("content_changed", "The RFQ changed after it was submitted. Return it to draft and submit again.")
    problems = await readiness(db, rfq)
    if problems:
        raise ApiError(409, "not_ready", " ".join(problems))
    rfq.status, rfq.approved_by, rfq.approved_at, rfq.updated_at = "approved", p.user.id, utcnow(), utcnow()
    rfq.decision_note = note
    event(db, p, rfq, "approved", from_state="pending_approval", to_state="approved", note=note,
          data={"content_sha256": rfq.content_sha256, "version": rfq.version})
    await db.commit()
    return rfq


async def return_to_draft(db: AsyncSession, p: Principal, rfq: Rfq, reason: str) -> Rfq:
    if rfq.status not in ("pending_approval", "approved"):
        raise conflict("invalid_state", "Only RFQs pending approval or approved (not yet sent) can be returned to draft")
    from_state = rfq.status
    rfq.status, rfq.version, rfq.content_sha256 = "draft", rfq.version + 1, None
    rfq.approved_by = rfq.approved_at = rfq.submitted_by = rfq.submitted_at = None
    rfq.decision_note, rfq.updated_at = reason, utcnow()
    event(db, p, rfq, "returned", from_state=from_state, to_state="draft", note=reason, data={"version": rfq.version})
    await db.commit()
    return rfq


# --- Packages & sending -------------------------------------------------------------------------------------------------
def _message_domain() -> str:
    address = parseaddr(settings.email_from)[1]
    return address.rsplit("@", 1)[1] if "@" in address else "localhost"


async def ensure_packages(db: AsyncSession, p: Principal, rfq: Rfq) -> list[RfqPackage]:
    """Build (once per RFQ version) one stored email package per invited supplier."""
    if rfq.status not in ("approved", "sent", "closed", "awarded"):
        raise conflict("not_approved", "Packages can only be generated for approved RFQs")
    if await current_hash(db, rfq) != rfq.content_sha256:
        raise conflict("content_changed", "The RFQ no longer matches what was approved")
    document = await document_for(db, rfq)
    existing = {pk.supplier_id: pk for pk in await db.scalars(
        select(RfqPackage).where(RfqPackage.rfq_id == rfq.id, RfqPackage.rfq_version == rfq.version)
    )}
    pdf: bytes | None = None
    packages: list[RfqPackage] = []
    storage = get_storage()
    for _, supplier in await invited(db, rfq):
        if supplier.id in existing:
            packages.append(existing[supplier.id])
            continue
        contacts = list(await db.scalars(select(SupplierContact).where(SupplierContact.supplier_id == supplier.id)))
        if not contacts:
            raise conflict("no_contact", f"{supplier.name} has no contact")
        if pdf is None:
            pdf = render.render_rfq_pdf(document, status="approved", content_hash=rfq.content_sha256,
                                        issued=(rfq.approved_at or utcnow()).date())
        raw, manifest = package.build_package(
            document, content_hash=rfq.content_sha256 or "", pdf=pdf, sender=settings.email_from,
            reply_to=p.user.email, reply_to_name=p.user.full_name,
            recipients=[{"name": c.name, "email": c.email, "is_primary": c.is_primary} for c in contacts],
            message_domain=_message_domain(),
        )
        key = f"{rfq.organization_id}/rfq-packages/{rfq.id}/v{rfq.version}/{supplier.id}.eml"
        try:
            backend = await storage.put(key, raw, "message/rfc822")
        except StorageError as exc:
            raise ApiError(503, "storage_unavailable", "File storage is unavailable. Try again shortly.") from exc
        pkg = RfqPackage(
            organization_id=rfq.organization_id, rfq_id=rfq.id, supplier_id=supplier.id, rfq_version=rfq.version,
            subject=package.parse_package(raw)["Subject"], to_addresses=manifest["to"] + manifest["cc"],
            storage_backend=backend, storage_key=key, sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw),
            manifest=manifest,
        )
        db.add(pkg)
        await db.flush()
        packages.append(pkg)
        event(db, p, rfq, "package_generated", data={"supplier": supplier.code, "package_id": str(pkg.id), "sha256": pkg.sha256})
    return packages


async def generate_packages(db: AsyncSession, p: Principal, rfq: Rfq) -> list[RfqPackage]:
    packages = await ensure_packages(db, p, rfq)
    await db.commit()
    return packages


async def _queue_send(db: AsyncSession, rfq: Rfq, pkg: RfqPackage) -> None:
    job = await queue.enqueue(db, org_id=rfq.organization_id, kind=SEND_JOB, payload={"package_id": str(pkg.id)},
                              dedupe_key=f"{SEND_JOB}:{pkg.id}")
    if job is None:
        raise conflict("already_sending", "This package is already being sent")
    pkg.send_status, pkg.send_error = "queued", None
    link = await db.scalar(select(RfqSupplier).where(RfqSupplier.rfq_id == rfq.id, RfqSupplier.supplier_id == pkg.supplier_id))
    if link is not None and link.status in ("invited", "failed"):
        link.status = "sending"


async def send(db: AsyncSession, p: Principal, rfq: Rfq) -> Rfq:
    if rfq.status != "approved":
        raise conflict("invalid_state", "Only approved RFQs can be sent")
    problems = await readiness(db, rfq)  # qualification or due date may have lapsed since approval
    if problems:
        raise ApiError(409, "not_ready", " ".join(problems))
    for pkg in await ensure_packages(db, p, rfq):
        if pkg.send_status in ("not_sent", "failed"):
            await _queue_send(db, rfq, pkg)
    rfq.status, rfq.sent_at, rfq.updated_at = "sent", utcnow(), utcnow()
    event(db, p, rfq, "sent", from_state="approved", to_state="sent", data={"version": rfq.version})
    await db.commit()
    return rfq


async def resend_package(db: AsyncSession, p: Principal, rfq: Rfq, package_id: uuid.UUID) -> RfqPackage:
    if rfq.status not in ("sent", "closed"):
        raise conflict("invalid_state", "Packages can only be re-sent after the RFQ was sent")
    pkg = await db.scalar(select(RfqPackage).where(RfqPackage.id == package_id, RfqPackage.rfq_id == rfq.id))
    if pkg is None:
        raise not_found("Package not found")
    if pkg.rfq_version != rfq.version:
        raise conflict("outdated_package", "This package belongs to an older version of the RFQ")
    if pkg.send_status != "failed":
        raise conflict("invalid_state", "Only failed packages can be re-sent")
    await _queue_send(db, rfq, pkg)
    event(db, p, rfq, "package_resend", data={"package_id": str(pkg.id)})
    await db.commit()
    return pkg


async def close(db: AsyncSession, p: Principal, rfq: Rfq, note: str | None) -> Rfq:
    if rfq.status != "sent":
        raise conflict("invalid_state", "Only sent RFQs can be closed")
    rfq.status, rfq.closed_at, rfq.updated_at = "closed", utcnow(), utcnow()
    event(db, p, rfq, "closed", from_state="sent", to_state="closed", note=note)
    await db.commit()
    return rfq


async def cancel(db: AsyncSession, p: Principal, rfq: Rfq, reason: str) -> Rfq:
    if rfq.status in ("awarded", "cancelled"):
        raise conflict("invalid_state", f"An RFQ that is {rfq.status} cannot be cancelled")
    from_state = rfq.status
    rfq.status, rfq.decision_note, rfq.updated_at = "cancelled", reason, utcnow()
    for pkg in await db.scalars(select(RfqPackage).where(RfqPackage.rfq_id == rfq.id, RfqPackage.send_status == "queued")):
        pkg.send_status, pkg.send_error = "failed", "RFQ cancelled before sending"
    event(db, p, rfq, "cancelled", from_state=from_state, to_state="cancelled", note=reason)
    await db.commit()
    return rfq


# --- Quotes -----------------------------------------------------------------------------------------------------------------
QUOTE_EDITABLE = ("sent", "closed")


async def _quote(db: AsyncSession, p: Principal, quote_id: uuid.UUID, *, lock: bool = False) -> tuple[RfqQuote, Rfq]:
    stmt = select(RfqQuote).where(RfqQuote.id == quote_id, RfqQuote.organization_id == p.org_id)
    quote = await db.scalar(stmt.with_for_update() if lock else stmt)
    if quote is None:
        raise not_found("Quote not found")
    rfq = await db.scalar(select(Rfq).where(Rfq.id == quote.rfq_id))
    assert rfq is not None
    return quote, rfq


def _require_quote_draft(quote: RfqQuote, rfq: Rfq) -> None:
    if rfq.status not in QUOTE_EDITABLE:
        raise conflict("rfq_not_open", f"Quotes cannot be changed while the RFQ is {rfq.status}")
    if quote.status != "draft":
        raise conflict("quote_verified", "Reopen the quote before changing it")


async def _write_lines(db: AsyncSession, rfq: Rfq, quote: RfqQuote, lines: list[dict[str, Any]]) -> None:
    items = await items_of(db, rfq)
    item_ids = {i.id for i in items}
    for ln in lines:
        if ln.get("rfq_item_id") and ln["rfq_item_id"] not in item_ids:
            raise bad_request("invalid_item", "A line refers to a requirement that is not on this RFQ")
    unmatched = [n for n, ln in enumerate(lines) if not ln.get("rfq_item_id")]
    suggestions = matching.suggest(
        [{"description": lines[n]["description"]} for n in unmatched],
        [{"id": i.id, "statement": i.statement, "parameters": i.parameters} for i in items
         if i.id not in {ln.get("rfq_item_id") for ln in lines}],
    )
    suggested = dict(zip(unmatched, suggestions, strict=True))
    await db.execute(delete(RfqQuoteLine).where(RfqQuoteLine.quote_id == quote.id))
    for n, ln in enumerate(lines):
        item_id, status, score = ln.get("rfq_item_id"), "confirmed" if ln.get("rfq_item_id") else "unmatched", 1.0 if ln.get("rfq_item_id") else 0.0
        if n in suggested and suggested[n][0] is not None:
            item_id, status, score = suggested[n][0], "suggested", suggested[n][1]
        db.add(RfqQuoteLine(
            organization_id=quote.organization_id, quote_id=quote.id, position=n, rfq_item_id=item_id, match_status=status,
            match_score=score, description=ln["description"].strip(), quantity=ln.get("quantity"), unit=ln.get("unit"),
            unit_price=ln.get("unit_price"), total_price=ln.get("total_price"), lead_time_days=ln.get("lead_time_days"),
            confidence=ln.get("confidence", 1.0), source_quote=ln.get("source_quote"),
        ))


async def _new_quote(db: AsyncSession, p: Principal, rfq: Rfq, supplier_id: uuid.UUID, header: dict[str, Any],
                     lines: list[dict[str, Any]], **extra: Any) -> RfqQuote:
    if rfq.status != "sent":
        raise conflict("rfq_not_open", "Quotes can only be recorded while the RFQ is out for quotation")
    link = await db.scalar(select(RfqSupplier).where(RfqSupplier.rfq_id == rfq.id, RfqSupplier.supplier_id == supplier_id))
    if link is None:
        raise conflict("not_invited", "This supplier was not invited to the RFQ")
    quote = RfqQuote(organization_id=rfq.organization_id, rfq_id=rfq.id, supplier_id=supplier_id, created_by=p.user.id,
                     **header, **extra)
    try:
        async with db.begin_nested():
            db.add(quote)
            await db.flush()
    except IntegrityError:
        raise conflict("quote_exists", "This supplier already has a quote on the RFQ; edit it instead") from None
    await _write_lines(db, rfq, quote, lines)
    link.status = "quoted"
    event(db, p, rfq, "quote_recorded", data={"quote_id": str(quote.id), "supplier_id": str(supplier_id),
                                             "source": quote.source, "lines": len(lines)})
    await db.flush()
    await db.refresh(quote)
    await db.commit()
    return quote


async def create_quote(db: AsyncSession, p: Principal, rfq: Rfq, body: rs.QuoteIn) -> RfqQuote:
    header = body.model_dump(include={"currency", "incoterms", "payment_terms", "valid_until", "notes"})
    return await _new_quote(db, p, rfq, body.supplier_id, header, [ln.model_dump() for ln in body.lines], source="manual")


def _known(field: Any) -> Any:
    return field.get("value") if isinstance(field, dict) and not field.get("unknown") and field.get("value") is not None else None


def quote_from_analysis(result: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """Map a grounded document analysis to a draft quote. Unknown stays unknown; nothing is inferred."""
    notes: list[str] = []
    currency = _known(result.get("currency"))
    currency = currency.upper() if isinstance(currency, str) and re.fullmatch(r"[A-Za-z]{3}", currency) else None
    incoterms = _known(result.get("incoterms"))
    incoterms = incoterms.upper() if isinstance(incoterms, str) and re.fullmatch(r"[A-Za-z]{3}", incoterms) else None
    valid_raw, valid = _known(result.get("valid_until")), None
    if valid_raw:
        try:
            valid = date.fromisoformat(str(valid_raw)[:10])
        except ValueError:
            notes.append(f"Validity stated as '{valid_raw}' could not be read as a date; enter it manually.")
    header = {"currency": currency, "incoterms": incoterms, "payment_terms": _known(result.get("payment_terms")),
              "valid_until": valid}
    lines = []
    for item in result.get("line_items") or []:
        fields = {k: item.get(k) or {} for k in ("quantity", "unit", "unit_price", "total_price", "lead_time_days")}
        confidences = [f.get("confidence", 0.0) for f in fields.values() if isinstance(f, dict) and not f.get("unknown")]
        lines.append({
            "description": item.get("description") or "Line", "quantity": _known(fields["quantity"]),
            "unit": _known(fields["unit"]), "unit_price": _known(fields["unit_price"]),
            "total_price": _known(fields["total_price"]), "lead_time_days": _known(fields["lead_time_days"]),
            "confidence": round(min(confidences), 3) if confidences else 0.0, "source_quote": item.get("source"),
            "rfq_item_id": None,
        })
    return header, lines, notes


async def quote_from_document(db: AsyncSession, p: Principal, rfq: Rfq, body: rs.QuoteFromDocumentIn) -> RfqQuote:
    doc = await db.scalar(select(Document).where(Document.id == body.document_id, Document.organization_id == p.org_id))
    if doc is None:
        raise not_found("Document not found")
    if doc.scan_status != "clean" or doc.analysis_status not in ("succeeded", "needs_review"):
        raise conflict("no_analysis", "The document needs a completed AI analysis first")
    extraction = await db.scalar(
        select(DocumentExtraction).where(DocumentExtraction.document_id == doc.id).order_by(DocumentExtraction.created_at.desc()).limit(1)
    )
    if extraction is None:
        raise conflict("no_analysis", "The document needs a completed AI analysis first")
    header, lines, notes = quote_from_analysis(extraction.result)
    if extraction.needs_review:
        notes.append("The analysis was flagged for review; check every value against the document.")
    header["notes"] = "\n".join(notes) or None
    return await _new_quote(db, p, rfq, body.supplier_id, header, lines, source="analysis", document_id=doc.id,
                            extraction_id=extraction.id)


async def update_quote(db: AsyncSession, p: Principal, quote_id: uuid.UUID, body: rs.QuoteHeader) -> RfqQuote:
    quote, rfq = await _quote(db, p, quote_id, lock=True)
    _require_quote_draft(quote, rfq)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(quote, key, value)
    quote.updated_at = utcnow()
    event(db, p, rfq, "quote_updated", data={"quote_id": str(quote.id)})
    await db.commit()
    return quote


async def replace_lines(db: AsyncSession, p: Principal, quote_id: uuid.UUID, body: rs.QuoteLinesIn) -> RfqQuote:
    quote, rfq = await _quote(db, p, quote_id, lock=True)
    _require_quote_draft(quote, rfq)
    await _write_lines(db, rfq, quote, [ln.model_dump() for ln in body.lines])
    quote.updated_at = utcnow()
    event(db, p, rfq, "quote_lines_replaced", data={"quote_id": str(quote.id), "lines": len(body.lines)})
    await db.commit()
    return quote


async def set_match(db: AsyncSession, p: Principal, quote_id: uuid.UUID, line_id: uuid.UUID, item_id: uuid.UUID | None) -> RfqQuote:
    quote, rfq = await _quote(db, p, quote_id, lock=True)
    _require_quote_draft(quote, rfq)
    line = await db.scalar(select(RfqQuoteLine).where(RfqQuoteLine.id == line_id, RfqQuoteLine.quote_id == quote.id))
    if line is None:
        raise not_found("Quote line not found")
    if item_id is not None and not await db.scalar(select(RfqItem.id).where(RfqItem.id == item_id, RfqItem.rfq_id == rfq.id)):
        raise bad_request("invalid_item", "That requirement is not on this RFQ")
    line.rfq_item_id = item_id
    line.match_status = "confirmed" if item_id else "unmatched"
    line.match_score = 1.0 if item_id else 0.0
    event(db, p, rfq, "quote_line_matched", data={"quote_id": str(quote.id), "line_id": str(line.id),
                                                 "rfq_item_id": str(item_id) if item_id else None})
    await db.commit()
    return quote


async def verify_quote(db: AsyncSession, p: Principal, quote_id: uuid.UUID) -> RfqQuote:
    quote, rfq = await _quote(db, p, quote_id, lock=True)
    _require_quote_draft(quote, rfq)
    lines = list(await db.scalars(select(RfqQuoteLine).where(RfqQuoteLine.quote_id == quote.id)))
    problems = []
    if not lines:
        problems.append("The quote has no lines.")
    if any(ln.match_status == "suggested" for ln in lines):
        problems.append("Confirm or clear every suggested line match.")
    if not quote.currency:
        problems.append("State the quote currency.")
    if problems:
        raise ApiError(409, "quote_incomplete", " ".join(problems))
    quote.status, quote.verified_by, quote.verified_at, quote.updated_at = "verified", p.user.id, utcnow(), utcnow()
    event(db, p, rfq, "quote_verified", data={"quote_id": str(quote.id), "supplier_id": str(quote.supplier_id)})
    await db.commit()
    return quote


async def reopen_quote(db: AsyncSession, p: Principal, quote_id: uuid.UUID, reason: str) -> RfqQuote:
    quote, rfq = await _quote(db, p, quote_id, lock=True)
    if rfq.status not in QUOTE_EDITABLE:
        raise conflict("rfq_not_open", f"Quotes cannot be changed while the RFQ is {rfq.status}")
    if quote.status != "verified":
        raise conflict("invalid_state", "Only verified quotes can be reopened")
    quote.status, quote.verified_by, quote.verified_at, quote.updated_at = "draft", None, None, utcnow()
    event(db, p, rfq, "quote_reopened", data={"quote_id": str(quote.id)}, note=reason)
    await db.commit()
    return quote


# --- Comparison & award ------------------------------------------------------------------------------------------------------
async def comparison_inputs(db: AsyncSession, rfq: Rfq) -> dict[str, Any]:
    items = await items_of(db, rfq)
    pairs = await invited(db, rfq)
    states = await supplier_svc.load_state(db, rfq.organization_id, [s.id for _, s in pairs])
    quotes = list(await db.scalars(select(RfqQuote).where(RfqQuote.rfq_id == rfq.id).order_by(RfqQuote.created_at)))
    lines: dict[uuid.UUID, list[RfqQuoteLine]] = {q.id: [] for q in quotes}
    if quotes:
        for ln in await db.scalars(select(RfqQuoteLine).where(RfqQuoteLine.quote_id.in_(lines)).order_by(RfqQuoteLine.position)):
            lines[ln.quote_id].append(ln)
    return {
        "currency": rfq.currency,
        "as_of": utcnow().date().isoformat(),
        "items": [{"id": str(i.id), "position": i.position, "statement": i.statement, "quantity": i.quantity,
                   "unit": i.unit, "priority": i.priority} for i in items],
        "suppliers": [{"id": str(s.id), "name": s.name, "code": s.code, "qualified": states[s.id].summary(s).qualified}
                      for _, s in pairs],
        "quotes": [{
            "id": str(q.id), "supplier_id": str(q.supplier_id), "status": q.status, "currency": q.currency,
            "incoterms": q.incoterms, "payment_terms": q.payment_terms,
            "valid_until": q.valid_until.isoformat() if q.valid_until else None,
            "lines": [{"rfq_item_id": str(ln.rfq_item_id) if ln.rfq_item_id else None, "match_status": ln.match_status,
                       "description": ln.description, "quantity": ln.quantity, "unit": ln.unit, "unit_price": ln.unit_price,
                       "total_price": ln.total_price, "lead_time_days": ln.lead_time_days, "confidence": ln.confidence}
                      for ln in lines[q.id]],
        } for q in quotes],
    }


async def run_comparison(db: AsyncSession, p: Principal, rfq: Rfq, weights: dict[str, float] | None) -> Comparison:
    if rfq.status not in ("sent", "closed", "awarded"):
        raise conflict("invalid_state", "Quotes can only be compared once the RFQ has been sent")
    inputs = await comparison_inputs(db, rfq)
    try:
        result = cmp.compare(inputs, weights, settings.comparison_outlier_ratio)
    except cmp.ComparisonError as exc:
        raise ApiError(422, "invalid_weights", str(exc)) from None
    row = Comparison(organization_id=rfq.organization_id, rfq_id=rfq.id, created_by=p.user.id,
                     params={"weights": result["weights"], "outlier_ratio": settings.comparison_outlier_ratio},
                     inputs_sha256=result["inputs_sha256"], result=result)
    db.add(row)
    await db.flush()
    event(db, p, rfq, "compared", data={"comparison_id": str(row.id), "weights": result["weights"],
                                       "scored": len(result["ranking"]), "excluded": len(result["excluded"])})
    await db.refresh(row)
    await db.commit()
    return row


async def is_stale(db: AsyncSession, rfq: Rfq, row: Comparison) -> bool:
    current = await comparison_inputs(db, rfq)
    current["as_of"] = row.result.get("as_of", current["as_of"])  # a new day alone does not make it stale
    return cmp.inputs_hash(current) != row.inputs_sha256


async def award(db: AsyncSession, p: Principal, rfq: Rfq, body: rs.AwardIn) -> Rfq:
    if rfq.status not in ("sent", "closed"):
        raise conflict("invalid_state", "Only sent or closed RFQs can be awarded")
    if settings.require_separate_approver and p.user.id == rfq.created_by:
        raise forbidden("The person who created the RFQ cannot also award it", "segregation_of_duties")
    row = await db.scalar(select(Comparison).where(Comparison.id == body.comparison_id, Comparison.rfq_id == rfq.id))
    if row is None:
        raise not_found("Comparison not found")
    if await is_stale(db, rfq, row):
        raise conflict("stale_comparison", "Quotes changed after this comparison. Run the comparison again first.")
    entry = next((r for r in row.result["ranking"] if r["supplier_id"] == str(body.supplier_id)), None)
    if entry is None:
        raise conflict("not_ranked", "Only a supplier with a verified, comparable quote in the comparison can be awarded")
    supplier = await db.scalar(select(Supplier).where(Supplier.id == body.supplier_id))
    if supplier is None or not await supplier_svc.is_qualified(db, supplier):
        raise conflict("supplier_not_qualified", "The supplier must be qualified at the time of award")
    deviates = row.result["highest_score_supplier_id"] != str(body.supplier_id)
    from_state = rfq.status
    rfq.status, rfq.awarded_supplier_id, rfq.award_comparison_id = "awarded", body.supplier_id, row.id
    rfq.awarded_by, rfq.awarded_at, rfq.award_note, rfq.updated_at = p.user.id, utcnow(), body.note.strip(), utcnow()
    event(db, p, rfq, "awarded", from_state=from_state, to_state="awarded", note=body.note,
          data={"supplier_id": str(body.supplier_id), "comparison_id": str(row.id), "rank": entry["rank"],
                "score": entry["score"], "deviates_from_ranking": deviates})
    await db.commit()
    return rfq
