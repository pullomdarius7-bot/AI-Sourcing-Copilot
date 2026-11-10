import csv
import io
import re
import uuid

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record
from app.db import get_db, set_tenant
from app.deps import Principal, require_permission
from app.errors import conflict, not_found
from app.events import emit_event
from app.models import (
    DomainEvent,
    QualificationCriterion,
    Supplier,
    SupplierCategory,
    SupplierCategoryLink,
    SupplierContact,
    SupplierQualificationCheck,
    User,
)
from app.rbac import Permission, has_permission
from app.schemas import MessageOut
from app.suppliers import schemas as sc
from app.suppliers import service as svc

router = APIRouter(tags=["suppliers"])
READ, MANAGE, QUALIFY = Permission.SUPPLIERS_READ, Permission.SUPPLIERS_MANAGE, Permission.SUPPLIERS_QUALIFY
STATUSES = "^(prospect|under_review|qualified|disqualified|blocked)$"


def contact_out(c: SupplierContact) -> sc.ContactOut:
    return sc.ContactOut(id=c.id, name=c.name, email=c.email, phone=c.phone, role=c.role, is_primary=c.is_primary)


async def supplier_out(db: AsyncSession, supplier: Supplier) -> sc.SupplierOut:
    await set_tenant(db, supplier.organization_id)  # services commit, which ends the tenant-scoped transaction
    state = (await svc.load_state(db, supplier.organization_id, [supplier.id]))[supplier.id]
    cats = list(await db.scalars(
        select(SupplierCategory).join(SupplierCategoryLink, SupplierCategoryLink.category_id == SupplierCategory.id)
        .where(SupplierCategoryLink.supplier_id == supplier.id).order_by(SupplierCategory.name)
    ))
    contacts = list(await db.scalars(
        select(SupplierContact).where(SupplierContact.supplier_id == supplier.id)
        .order_by(SupplierContact.is_primary.desc(), SupplierContact.created_at)
    ))
    return sc.SupplierOut(
        id=supplier.id, code=supplier.code, name=supplier.name, legal_name=supplier.legal_name, status=supplier.status,
        country=supplier.country, website=supplier.website, tax_id=supplier.tax_id, address=supplier.address,
        notes=supplier.notes, payment_terms=supplier.payment_terms, currency=supplier.currency,
        typical_lead_time_days=supplier.typical_lead_time_days, tags=supplier.tags,
        categories=[svc.category_out(c) for c in cats], contacts=[contact_out(c) for c in contacts],
        qualification=state.summary(supplier), submitted_by=supplier.submitted_by, submitted_at=supplier.submitted_at,
        decided_by=supplier.decided_by, decided_at=supplier.decided_at, status_note=supplier.status_note,
        created_at=supplier.created_at,
    )


def criterion_out(c: QualificationCriterion) -> sc.CriterionOut:
    return sc.CriterionOut(id=c.id, key=c.key, name=c.name, description=c.description, required=c.required,
                           weight=c.weight, active=c.active)


# --- Categories -------------------------------------------------------------------------------------------------
@router.get("/supplier-categories", response_model=list[sc.CategoryOut])
async def list_categories(p: Principal = Depends(require_permission(READ)), db: AsyncSession = Depends(get_db)):
    counts = dict((await db.execute(
        select(SupplierCategoryLink.category_id, func.count())
        .where(SupplierCategoryLink.organization_id == p.org_id).group_by(SupplierCategoryLink.category_id)
    )).all())
    cats = await db.scalars(
        select(SupplierCategory).where(SupplierCategory.organization_id == p.org_id).order_by(SupplierCategory.name)
    )
    return [svc.category_out(c, counts.get(c.id, 0)) for c in cats]


@router.post("/supplier-categories", response_model=sc.CategoryOut, status_code=201)
async def create_category(body: sc.CategoryIn, p: Principal = Depends(require_permission(MANAGE)),
                          db: AsyncSession = Depends(get_db)):
    return svc.category_out(await svc.create_category(db, p, body))


# --- Qualification criteria ----------------------------------------------------------------------------------------------
@router.get("/qualification-criteria", response_model=list[sc.CriterionOut])
async def list_criteria(p: Principal = Depends(require_permission(READ)), db: AsyncSession = Depends(get_db)):
    await svc.ensure_default_criteria(db, p.org_id)
    rows = await db.scalars(
        select(QualificationCriterion).where(QualificationCriterion.organization_id == p.org_id)
        .order_by(QualificationCriterion.created_at, QualificationCriterion.key)
    )
    return [criterion_out(c) for c in rows]


@router.post("/qualification-criteria", response_model=sc.CriterionOut, status_code=201)
async def create_criterion(body: sc.CriterionIn, p: Principal = Depends(require_permission(QUALIFY)),
                           db: AsyncSession = Depends(get_db)):
    await svc.ensure_default_criteria(db, p.org_id)
    crit = QualificationCriterion(organization_id=p.org_id, **body.model_dump())
    try:
        async with db.begin_nested():
            db.add(crit)
            await db.flush()
    except IntegrityError:
        raise conflict("criterion_exists", "A criterion with this key already exists") from None
    emit_event(db, org_id=p.org_id, entity_type="criterion", entity_id=crit.id, action="created", actor=p.user.id,
               data=body.model_dump(), ip=p.ip)
    await db.refresh(crit)
    await db.commit()
    return criterion_out(crit)


@router.patch("/qualification-criteria/{criterion_id}", response_model=sc.CriterionOut)
async def update_criterion(criterion_id: uuid.UUID, body: sc.CriterionUpdate,
                           p: Principal = Depends(require_permission(QUALIFY)), db: AsyncSession = Depends(get_db)):
    crit = await db.scalar(select(QualificationCriterion).where(
        QualificationCriterion.id == criterion_id, QualificationCriterion.organization_id == p.org_id
    ))
    if crit is None:
        raise not_found("Criterion not found")
    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    for k, v in changes.items():
        setattr(crit, k, v)
    emit_event(db, org_id=p.org_id, entity_type="criterion", entity_id=crit.id, action="updated", actor=p.user.id,
               data=changes, ip=p.ip)
    await db.commit()
    return criterion_out(crit)


# --- Suppliers ---------------------------------------------------------------------------------------------------------------
def _filters(p: Principal, status: str | None, q: str | None, category_id: uuid.UUID | None, country: str | None,
             tag: str | None) -> list:
    conds = [Supplier.organization_id == p.org_id]
    if status:
        conds.append(Supplier.status == status)
    if q:
        pattern = "%" + re.sub(r"([\\%_])", r"\\\1", q.strip()) + "%"
        conds.append(Supplier.name.ilike(pattern, escape="\\") | Supplier.code.ilike(pattern, escape="\\"))
    if category_id:
        conds.append(Supplier.id.in_(
            select(SupplierCategoryLink.supplier_id).where(SupplierCategoryLink.category_id == category_id)
        ))
    if country:
        conds.append(Supplier.country == country.upper())
    if tag:
        conds.append(Supplier.tags.contains([tag.lower()]))
    return conds


@router.get("/suppliers/export")
async def export_suppliers(
    status: str | None = Query(None, pattern=STATUSES), category_id: uuid.UUID | None = None,
    p: Principal = Depends(require_permission(READ)), db: AsyncSession = Depends(get_db),
):
    suppliers = list(await db.scalars(
        select(Supplier).where(*_filters(p, status, None, category_id, None, None)).order_by(Supplier.code)
    ))
    ids = [s.id for s in suppliers]
    states = await svc.load_state(db, p.org_id, ids)
    cats: dict[uuid.UUID, list[str]] = {i: [] for i in ids}
    primary: dict[uuid.UUID, SupplierContact] = {}
    if ids:
        for sid, name in (await db.execute(
            select(SupplierCategoryLink.supplier_id, SupplierCategory.name)
            .join(SupplierCategory, SupplierCategory.id == SupplierCategoryLink.category_id)
            .where(SupplierCategoryLink.supplier_id.in_(ids))
        )).all():
            cats[sid].append(name)
        for c in await db.scalars(select(SupplierContact).where(SupplierContact.supplier_id.in_(ids), SupplierContact.is_primary.is_(True))):
            primary[c.supplier_id] = c
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["code", "name", "status", "qualified", "qualification_score", "country", "categories",
                     "primary_contact", "primary_email", "payment_terms", "currency", "typical_lead_time_days", "tags"])
    for s in suppliers:
        c = primary.get(s.id)
        writer.writerow([
            s.code, _csv_safe(s.name), s.status, states[s.id].summary(s).qualified, states[s.id].score, s.country or "",
            _csv_safe("; ".join(sorted(cats[s.id]))), _csv_safe(c.name) if c else "", c.email if c else "",
            _csv_safe(s.payment_terms or ""), s.currency or "", s.typical_lead_time_days if s.typical_lead_time_days is not None else "",
            _csv_safe("; ".join(s.tags)),
        ])
    record(db, org_id=p.org_id, user_id=p.user.id, action="supplier.exported", ip=p.ip, meta={"count": len(suppliers)})
    await db.commit()
    return Response(
        "﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="suppliers.csv"'},
    )


def _csv_safe(value: str) -> str:
    """Neutralise spreadsheet formula injection in user-supplied text."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


@router.get("/suppliers", response_model=sc.SupplierListOut)
async def list_suppliers(
    status: str | None = Query(None, pattern=STATUSES), q: str | None = Query(None, max_length=100),
    category_id: uuid.UUID | None = None, country: str | None = Query(None, min_length=2, max_length=2),
    tag: str | None = Query(None, max_length=40), limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0),
    p: Principal = Depends(require_permission(READ)), db: AsyncSession = Depends(get_db),
):
    conds = _filters(p, status, q, category_id, country, tag)
    total = await db.scalar(select(func.count()).select_from(Supplier).where(*conds)) or 0
    suppliers = list(await db.scalars(
        select(Supplier).where(*conds).order_by(Supplier.name, Supplier.id).limit(limit).offset(offset)
    ))
    ids = [s.id for s in suppliers]
    states = await svc.load_state(db, p.org_id, ids)
    cats: dict[uuid.UUID, list[str]] = {i: [] for i in ids}
    counts: dict[uuid.UUID, int] = dict.fromkeys(ids, 0)
    if ids:
        for sid, name in (await db.execute(
            select(SupplierCategoryLink.supplier_id, SupplierCategory.name)
            .join(SupplierCategory, SupplierCategory.id == SupplierCategoryLink.category_id)
            .where(SupplierCategoryLink.supplier_id.in_(ids))
        )).all():
            cats[sid].append(name)
        for sid, n in (await db.execute(
            select(SupplierContact.supplier_id, func.count()).where(SupplierContact.supplier_id.in_(ids))
            .group_by(SupplierContact.supplier_id)
        )).all():
            counts[sid] = n
    return sc.SupplierListOut(
        items=[sc.SupplierListItem(
            id=s.id, code=s.code, name=s.name, status=s.status, country=s.country, categories=sorted(cats[s.id]),
            contact_count=counts[s.id], qualification=states[s.id].summary(s),
        ) for s in suppliers],
        total=total,
    )


@router.post("/suppliers", response_model=sc.SupplierOut, status_code=201)
async def create_supplier(body: sc.SupplierIn, p: Principal = Depends(require_permission(MANAGE)),
                          db: AsyncSession = Depends(get_db)):
    return await supplier_out(db, await svc.create_supplier(db, p, body))


@router.get("/suppliers/{supplier_id}", response_model=sc.SupplierOut)
async def get_supplier(supplier_id: uuid.UUID, p: Principal = Depends(require_permission(READ)),
                       db: AsyncSession = Depends(get_db)):
    return await supplier_out(db, await svc.get_supplier(db, p, supplier_id))


@router.patch("/suppliers/{supplier_id}", response_model=sc.SupplierOut)
async def update_supplier(supplier_id: uuid.UUID, body: sc.SupplierUpdate,
                          p: Principal = Depends(require_permission(MANAGE)), db: AsyncSession = Depends(get_db)):
    supplier = await svc.get_supplier(db, p, supplier_id, lock=True)
    return await supplier_out(db, await svc.update_supplier(db, p, supplier, body))


@router.put("/suppliers/{supplier_id}/categories", response_model=sc.SupplierOut)
async def set_categories(supplier_id: uuid.UUID, body: sc.CategoryIdsIn,
                         p: Principal = Depends(require_permission(MANAGE)), db: AsyncSession = Depends(get_db)):
    supplier = await svc.get_supplier(db, p, supplier_id)
    await svc.set_categories(db, p, supplier, body.category_ids)
    return await supplier_out(db, supplier)


@router.post("/suppliers/{supplier_id}/contacts", response_model=sc.ContactOut, status_code=201)
async def add_contact(supplier_id: uuid.UUID, body: sc.ContactIn, p: Principal = Depends(require_permission(MANAGE)),
                      db: AsyncSession = Depends(get_db)):
    return contact_out(await svc.add_contact(db, p, await svc.get_supplier(db, p, supplier_id, lock=True), body))


@router.patch("/suppliers/{supplier_id}/contacts/{contact_id}", response_model=sc.ContactOut)
async def update_contact(supplier_id: uuid.UUID, contact_id: uuid.UUID, body: sc.ContactUpdate,
                         p: Principal = Depends(require_permission(MANAGE)), db: AsyncSession = Depends(get_db)):
    supplier = await svc.get_supplier(db, p, supplier_id, lock=True)
    return contact_out(await svc.update_contact(db, p, supplier, contact_id, body))


@router.delete("/suppliers/{supplier_id}/contacts/{contact_id}", response_model=MessageOut)
async def remove_contact(supplier_id: uuid.UUID, contact_id: uuid.UUID,
                         p: Principal = Depends(require_permission(MANAGE)), db: AsyncSession = Depends(get_db)):
    await svc.remove_contact(db, p, await svc.get_supplier(db, p, supplier_id, lock=True), contact_id)
    return MessageOut(message="Contact removed")


# --- Qualification ---------------------------------------------------------------------------------------------------------------
async def qualification_out(db: AsyncSession, supplier: Supplier) -> sc.QualificationOut:
    await set_tenant(db, supplier.organization_id)
    state = (await svc.load_state(db, supplier.organization_id, [supplier.id]))[supplier.id]
    criteria = list(await db.scalars(
        select(QualificationCriterion).where(QualificationCriterion.organization_id == supplier.organization_id)
        .order_by(QualificationCriterion.created_at, QualificationCriterion.key)
    ))
    checks = {c.criterion_id: c for c in await db.scalars(
        select(SupplierQualificationCheck).where(SupplierQualificationCheck.supplier_id == supplier.id)
    )}
    today = svc.utcnow().date()
    out = []
    for crit in criteria:
        chk = checks.get(crit.id)
        out.append(sc.CheckOut(
            criterion=criterion_out(crit), status=chk.status if chk else "pending",
            effective_status=state.effective.get(crit.id, svc.effective_status(chk, today)),
            note=chk.note if chk else None, expires_at=chk.expires_at if chk else None,
            evidence_document_id=chk.evidence_document_id if chk else None,
            checked_by=chk.checked_by if chk else None, checked_at=chk.checked_at if chk else None,
        ))
    return sc.QualificationOut(supplier_id=supplier.id, status=supplier.status, summary=state.summary(supplier), checks=out)


@router.get("/suppliers/{supplier_id}/qualification", response_model=sc.QualificationOut)
async def get_qualification(supplier_id: uuid.UUID, p: Principal = Depends(require_permission(READ)),
                            db: AsyncSession = Depends(get_db)):
    return await qualification_out(db, await svc.get_supplier(db, p, supplier_id))


@router.put("/suppliers/{supplier_id}/qualification/{criterion_id}", response_model=sc.QualificationOut)
async def record_check(supplier_id: uuid.UUID, criterion_id: uuid.UUID, body: sc.CheckIn,
                       p: Principal = Depends(require_permission(MANAGE)), db: AsyncSession = Depends(get_db)):
    supplier = await svc.get_supplier(db, p, supplier_id, lock=True)
    await svc.record_check(db, p, supplier, criterion_id, body, has_permission(p.user.role, QUALIFY))
    return await qualification_out(db, supplier)


@router.post("/suppliers/{supplier_id}/submit", response_model=sc.SupplierOut)
async def submit(supplier_id: uuid.UUID, p: Principal = Depends(require_permission(MANAGE)),
                 db: AsyncSession = Depends(get_db)):
    supplier = await svc.get_supplier(db, p, supplier_id, lock=True)
    return await supplier_out(db, await svc.submit_for_qualification(db, p, supplier))


@router.post("/suppliers/{supplier_id}/qualify", response_model=sc.SupplierOut)
async def qualify(supplier_id: uuid.UUID, body: sc.NoteIn, p: Principal = Depends(require_permission(QUALIFY)),
                  db: AsyncSession = Depends(get_db)):
    supplier = await svc.get_supplier(db, p, supplier_id, lock=True)
    return await supplier_out(db, await svc.qualify(db, p, supplier, body.note))


@router.post("/suppliers/{supplier_id}/disqualify", response_model=sc.SupplierOut)
async def disqualify(supplier_id: uuid.UUID, body: sc.ReasonIn, p: Principal = Depends(require_permission(QUALIFY)),
                     db: AsyncSession = Depends(get_db)):
    supplier = await svc.get_supplier(db, p, supplier_id, lock=True)
    return await supplier_out(db, await svc.disqualify(db, p, supplier, body.reason))


@router.post("/suppliers/{supplier_id}/block", response_model=sc.SupplierOut)
async def block(supplier_id: uuid.UUID, body: sc.ReasonIn, p: Principal = Depends(require_permission(QUALIFY)),
                db: AsyncSession = Depends(get_db)):
    supplier = await svc.get_supplier(db, p, supplier_id, lock=True)
    return await supplier_out(db, await svc.block(db, p, supplier, body.reason))


@router.post("/suppliers/{supplier_id}/reopen", response_model=sc.SupplierOut)
async def reopen(supplier_id: uuid.UUID, body: sc.NoteIn, p: Principal = Depends(require_permission(QUALIFY)),
                 db: AsyncSession = Depends(get_db)):
    supplier = await svc.get_supplier(db, p, supplier_id, lock=True)
    return await supplier_out(db, await svc.reopen(db, p, supplier, body.note))


async def entity_events(db: AsyncSession, org_id: uuid.UUID, entity_type: str, entity_id: uuid.UUID) -> list[sc.EventOut]:
    rows = (await db.execute(
        select(DomainEvent, User.email).join(User, User.id == DomainEvent.actor_id, isouter=True)
        .where(DomainEvent.organization_id == org_id, DomainEvent.entity_type == entity_type,
               DomainEvent.entity_id == entity_id)
        .order_by(DomainEvent.id.desc()).limit(300)
    )).all()
    return [sc.EventOut(id=e.id, action=e.action, actor_id=e.actor_id, actor_email=email, from_state=e.from_state,
                        to_state=e.to_state, data=e.data, note=e.note, created_at=e.created_at) for e, email in rows]


@router.get("/suppliers/{supplier_id}/events", response_model=list[sc.EventOut])
async def supplier_events(supplier_id: uuid.UUID, p: Principal = Depends(require_permission(READ)),
                          db: AsyncSession = Depends(get_db)):
    supplier = await svc.get_supplier(db, p, supplier_id)
    return await entity_events(db, p.org_id, "supplier", supplier.id)
