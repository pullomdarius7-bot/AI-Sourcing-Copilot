"""Supplier database: profiles, categorisation, contacts and the qualification workflow."""
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals import second_person_required
from app.config import get_settings
from app.deps import Principal
from app.errors import ApiError, bad_request, conflict, forbidden, not_found
from app.events import emit_event, next_number
from app.models import (
    QualificationCriterion,
    Supplier,
    SupplierCategory,
    SupplierCategoryLink,
    SupplierContact,
    SupplierQualificationCheck,
)
from app.suppliers import schemas as sc

settings = get_settings()

DEFAULT_CRITERIA = [
    ("quality_certification", "Quality management certification (e.g. ISO 9001)", True, 5),
    ("sanctions_screening", "Sanctions and restricted-party screening", True, 5),
    ("code_of_conduct", "Supplier code of conduct acknowledged", True, 3),
    ("insurance", "Liability insurance certificate", False, 3),
    ("financial_stability", "Financial stability review", False, 4),
    ("references", "Customer references checked", False, 2),
]


def utcnow() -> datetime:
    return datetime.now(UTC)


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:100] or "category"


# --- Criteria & qualification state ----------------------------------------------------------------------
async def ensure_default_criteria(db: AsyncSession, org_id: uuid.UUID) -> None:
    """Seed the default checklist once per organization, in its own transaction so read-only requests keep it."""
    exists = await db.scalar(
        select(func.count()).select_from(QualificationCriterion).where(QualificationCriterion.organization_id == org_id)
    )
    if exists:
        return
    from app.db import SessionLocal, set_tenant

    async with SessionLocal() as seed:
        await set_tenant(seed, org_id)
        if await seed.scalar(
            select(func.count()).select_from(QualificationCriterion).where(QualificationCriterion.organization_id == org_id)
        ):
            return
        for key, name, required, weight in DEFAULT_CRITERIA:
            seed.add(QualificationCriterion(organization_id=org_id, key=key, name=name, required=required, weight=weight))
        try:
            await seed.commit()
        except IntegrityError:  # a concurrent request seeded first
            await seed.rollback()


@dataclass
class QualState:
    score: int
    missing_required: list[str]
    expired: list[str]
    effective: dict[uuid.UUID, str]  # criterion id -> effective status

    def summary(self, supplier: Supplier) -> sc.QualificationSummary:
        ready = not self.missing_required
        return sc.QualificationSummary(
            score=self.score, ready=ready, missing_required=self.missing_required, expired=self.expired,
            qualified=supplier.status == "qualified" and ready,
        )


def effective_status(check: SupplierQualificationCheck | None, today: date) -> str:
    if check is None:
        return "pending"
    if check.status == "passed" and check.expires_at and check.expires_at < today:
        return "expired"
    return check.status


def compute_state(
    criteria: list[QualificationCriterion], checks: list[SupplierQualificationCheck], today: date | None = None
) -> QualState:
    today = today or utcnow().date()
    by_criterion = {c.criterion_id: c for c in checks}
    active = [c for c in criteria if c.active]
    total = sum(c.weight for c in active) or 1
    earned, missing, expired = 0, [], []
    effective: dict[uuid.UUID, str] = {}
    for crit in active:
        status = effective_status(by_criterion.get(crit.id), today)
        effective[crit.id] = status
        if status in ("passed", "waived"):
            earned += crit.weight
        if status == "expired":
            expired.append(crit.name)
        if crit.required and status not in ("passed", "waived"):
            missing.append(crit.name)
    return QualState(round(100 * earned / total), missing, expired, effective)


async def load_state(db: AsyncSession, org_id: uuid.UUID, supplier_ids: list[uuid.UUID]) -> dict[uuid.UUID, QualState]:
    await ensure_default_criteria(db, org_id)
    criteria = list(await db.scalars(select(QualificationCriterion).where(QualificationCriterion.organization_id == org_id)))
    checks: dict[uuid.UUID, list[SupplierQualificationCheck]] = {sid: [] for sid in supplier_ids}
    if supplier_ids:
        for chk in await db.scalars(
            select(SupplierQualificationCheck).where(SupplierQualificationCheck.supplier_id.in_(supplier_ids))
        ):
            checks[chk.supplier_id].append(chk)
    return {sid: compute_state(criteria, checks[sid]) for sid in supplier_ids}


async def is_qualified(db: AsyncSession, supplier: Supplier) -> bool:
    state = (await load_state(db, supplier.organization_id, [supplier.id]))[supplier.id]
    return state.summary(supplier).qualified


# --- Categories -------------------------------------------------------------------------------------------------
def category_out(cat: SupplierCategory, count: int = 0) -> sc.CategoryOut:
    return sc.CategoryOut(
        id=cat.id, name=cat.name, slug=cat.slug, parent_id=cat.parent_id, description=cat.description,
        supplier_count=count,
    )


async def create_category(db: AsyncSession, p: Principal, body: sc.CategoryIn) -> SupplierCategory:
    if body.parent_id and not await db.scalar(
        select(SupplierCategory.id).where(SupplierCategory.id == body.parent_id, SupplierCategory.organization_id == p.org_id)
    ):
        raise bad_request("invalid_parent", "The parent category does not exist")
    cat = SupplierCategory(
        organization_id=p.org_id, name=body.name.strip(), slug=slugify(body.name), parent_id=body.parent_id,
        description=body.description,
    )
    try:
        async with db.begin_nested():
            db.add(cat)
            await db.flush()
    except IntegrityError:
        raise conflict("category_exists", "A category with this name already exists") from None
    emit_event(db, org_id=p.org_id, entity_type="category", entity_id=cat.id, action="created", actor=p.user.id,
               data={"name": cat.name}, ip=p.ip)
    await db.refresh(cat)
    await db.commit()
    return cat


# --- Suppliers ---------------------------------------------------------------------------------------------------
async def validate_categories(db: AsyncSession, org_id: uuid.UUID, ids: list[uuid.UUID]) -> list[uuid.UUID]:
    ids = list(dict.fromkeys(ids))
    if ids:
        found = set(await db.scalars(
            select(SupplierCategory.id).where(SupplierCategory.id.in_(ids), SupplierCategory.organization_id == org_id)
        ))
        if found != set(ids):
            raise bad_request("invalid_category", "One or more categories do not exist")
    return ids


def normalise_contacts(contacts: list[sc.ContactIn]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for c in contacts:
        email = c.email.lower()
        if email in seen:
            raise bad_request("duplicate_contact", f"{email} is listed twice")
        seen.add(email)
        out.append({"name": c.name.strip(), "email": email, "phone": c.phone, "role": c.role, "is_primary": c.is_primary})
    if out:
        first_primary = next((i for i, c in enumerate(out) if c["is_primary"]), 0)
        for i, c in enumerate(out):
            c["is_primary"] = i == first_primary
    return out


async def create_supplier(db: AsyncSession, p: Principal, body: sc.SupplierIn) -> Supplier:
    category_ids = await validate_categories(db, p.org_id, body.category_ids)
    contacts = normalise_contacts(body.contacts)
    number = await next_number(db, p.org_id, "supplier")
    supplier = Supplier(
        organization_id=p.org_id, code=f"{settings.supplier_code_prefix}-{number:04d}", name=body.name.strip(),
        legal_name=body.legal_name, country=body.country.upper() if body.country else None, website=body.website,
        tax_id=body.tax_id, address=body.address, notes=body.notes, payment_terms=body.payment_terms,
        currency=body.currency.upper() if body.currency else None, typical_lead_time_days=body.typical_lead_time_days,
        tags=body.tags or [], created_by=p.user.id,
    )
    try:
        async with db.begin_nested():
            db.add(supplier)
            await db.flush()
    except IntegrityError:
        raise conflict("supplier_exists", "A supplier with this name already exists") from None
    for cid in category_ids:
        db.add(SupplierCategoryLink(organization_id=p.org_id, supplier_id=supplier.id, category_id=cid))
    for c in contacts:
        db.add(SupplierContact(organization_id=p.org_id, supplier_id=supplier.id, **c))
    emit_event(db, org_id=p.org_id, entity_type="supplier", entity_id=supplier.id, action="created", actor=p.user.id,
               to_state="prospect", data={"code": supplier.code, "name": supplier.name}, ip=p.ip)
    await db.refresh(supplier)
    await db.commit()
    return supplier


async def get_supplier(db: AsyncSession, p: Principal, supplier_id: uuid.UUID, *, lock: bool = False) -> Supplier:
    stmt = select(Supplier).where(Supplier.id == supplier_id, Supplier.organization_id == p.org_id)
    supplier = await db.scalar(stmt.with_for_update() if lock else stmt)
    if supplier is None:
        raise not_found("Supplier not found")
    return supplier


async def update_supplier(db: AsyncSession, p: Principal, supplier: Supplier, body: sc.SupplierUpdate) -> Supplier:
    changes: dict[str, Any] = {}
    for field, value in body.model_dump(exclude_unset=True).items():
        if field == "name":
            if value is None:
                continue
            value = value.strip()
        if field in ("country", "currency") and value:
            value = value.upper()
        if field == "tags" and value is None:
            value = []
        if getattr(supplier, field) != value:
            changes[field] = {"from": getattr(supplier, field), "to": value}
            setattr(supplier, field, value)
    if changes:
        supplier.updated_at = utcnow()
        try:
            async with db.begin_nested():
                await db.flush()
        except IntegrityError:
            raise conflict("supplier_exists", "A supplier with this name already exists") from None
        emit_event(db, org_id=p.org_id, entity_type="supplier", entity_id=supplier.id, action="updated",
                   actor=p.user.id, data=changes, ip=p.ip)
    await db.commit()
    return supplier


async def set_categories(db: AsyncSession, p: Principal, supplier: Supplier, ids: list[uuid.UUID]) -> None:
    ids = await validate_categories(db, p.org_id, ids)
    await db.execute(delete(SupplierCategoryLink).where(SupplierCategoryLink.supplier_id == supplier.id))
    for cid in ids:
        db.add(SupplierCategoryLink(organization_id=p.org_id, supplier_id=supplier.id, category_id=cid))
    emit_event(db, org_id=p.org_id, entity_type="supplier", entity_id=supplier.id, action="categories_set",
               actor=p.user.id, data={"category_ids": [str(i) for i in ids]}, ip=p.ip)
    await db.commit()


async def _clear_primary(db: AsyncSession, supplier_id: uuid.UUID) -> None:
    """Clear the current primary first: a partial unique index allows only one primary contact per supplier."""
    await db.execute(update(SupplierContact).where(SupplierContact.supplier_id == supplier_id).values(is_primary=False))


async def add_contact(db: AsyncSession, p: Principal, supplier: Supplier, body: sc.ContactIn) -> SupplierContact:
    existing = list(await db.scalars(select(SupplierContact).where(SupplierContact.supplier_id == supplier.id)))
    email = body.email.lower()
    if any(c.email == email for c in existing):
        raise conflict("contact_exists", "This supplier already has a contact with that email")
    primary = body.is_primary or not existing
    if primary:
        await _clear_primary(db, supplier.id)
    contact = SupplierContact(
        organization_id=p.org_id, supplier_id=supplier.id, name=body.name.strip(), email=email, phone=body.phone,
        role=body.role, is_primary=primary,
    )
    db.add(contact)
    await db.flush()
    emit_event(db, org_id=p.org_id, entity_type="supplier", entity_id=supplier.id, action="contact_added",
               actor=p.user.id, data={"email": email, "primary": primary}, ip=p.ip)
    await db.refresh(contact)
    await db.commit()
    return contact


async def update_contact(
    db: AsyncSession, p: Principal, supplier: Supplier, contact_id: uuid.UUID, body: sc.ContactUpdate
) -> SupplierContact:
    contacts = list(await db.scalars(select(SupplierContact).where(SupplierContact.supplier_id == supplier.id)))
    contact = next((c for c in contacts if c.id == contact_id), None)
    if contact is None:
        raise not_found("Contact not found")
    data = body.model_dump(exclude_unset=True)
    if data.get("is_primary") is False and contact.is_primary:
        raise bad_request("primary_required", "Make another contact primary instead")
    if data.get("email"):
        email = data["email"].lower()
        if any(c.email == email and c.id != contact.id for c in contacts):
            raise conflict("contact_exists", "This supplier already has a contact with that email")
        contact.email = email
    if data.get("name"):
        contact.name = data["name"].strip()
    for field in ("phone", "role"):
        if field in data:
            setattr(contact, field, data[field])
    if data.get("is_primary") is True and not contact.is_primary:
        await db.flush()
        await _clear_primary(db, supplier.id)
        await db.refresh(contact)
        contact.is_primary = True
    emit_event(db, org_id=p.org_id, entity_type="supplier", entity_id=supplier.id, action="contact_updated",
               actor=p.user.id, data={"email": contact.email}, ip=p.ip)
    await db.flush()
    await db.refresh(contact)
    await db.commit()
    return contact


async def remove_contact(db: AsyncSession, p: Principal, supplier: Supplier, contact_id: uuid.UUID) -> None:
    contacts = list(await db.scalars(
        select(SupplierContact).where(SupplierContact.supplier_id == supplier.id).order_by(SupplierContact.created_at)
    ))
    contact = next((c for c in contacts if c.id == contact_id), None)
    if contact is None:
        raise not_found("Contact not found")
    rest = [c for c in contacts if c.id != contact.id]
    if supplier.status == "qualified" and not rest:
        raise conflict("last_contact", "A qualified supplier must keep at least one contact")
    await db.execute(delete(SupplierContact).where(SupplierContact.id == contact.id))
    if contact.is_primary and rest:
        rest[0].is_primary = True
    emit_event(db, org_id=p.org_id, entity_type="supplier", entity_id=supplier.id, action="contact_removed",
               actor=p.user.id, data={"email": contact.email}, ip=p.ip)
    await db.commit()


# --- Qualification workflow -------------------------------------------------------------------------------------------
async def record_check(
    db: AsyncSession, p: Principal, supplier: Supplier, criterion_id: uuid.UUID, body: sc.CheckIn, can_waive: bool
) -> SupplierQualificationCheck:
    await ensure_default_criteria(db, p.org_id)
    crit = await db.scalar(select(QualificationCriterion).where(
        QualificationCriterion.id == criterion_id, QualificationCriterion.organization_id == p.org_id
    ))
    if crit is None:
        raise not_found("Criterion not found")
    if supplier.status == "blocked":
        raise conflict("supplier_blocked", "Reopen a blocked supplier before recording checks")
    if body.status == "waived":
        if not can_waive:
            raise forbidden("Only a qualifier can waive a check", "waiver_not_allowed")
        if not body.note or len(body.note.strip()) < 10:
            raise ApiError(422, "note_required", "Explain why the check is waived (at least 10 characters)")
    if body.status == "failed" and not (body.note and body.note.strip()):
        raise ApiError(422, "note_required", "Say why the check failed")
    if body.expires_at and body.status != "passed":
        raise ApiError(422, "expiry_not_applicable", "Only passed checks can expire")
    if body.status == "passed" and body.expires_at and body.expires_at < utcnow().date():
        raise ApiError(422, "already_expired", "The evidence has already expired")
    check = await db.scalar(select(SupplierQualificationCheck).where(
        SupplierQualificationCheck.supplier_id == supplier.id, SupplierQualificationCheck.criterion_id == crit.id
    ).with_for_update())
    before = check.status if check else "pending"
    if check is None:
        check = SupplierQualificationCheck(organization_id=p.org_id, supplier_id=supplier.id, criterion_id=crit.id)
        db.add(check)
    check.status, check.note, check.expires_at = body.status, body.note, body.expires_at
    check.evidence_document_id = body.evidence_document_id
    check.checked_by, check.checked_at = p.user.id, utcnow()
    await db.flush()
    emit_event(db, org_id=p.org_id, entity_type="supplier", entity_id=supplier.id, action="check_recorded",
               actor=p.user.id, from_state=before, to_state=body.status, data={"criterion": crit.key}, note=body.note,
               ip=p.ip)
    state = (await load_state(db, p.org_id, [supplier.id]))[supplier.id]
    if supplier.status == "qualified" and state.missing_required:
        supplier.status, supplier.updated_at = "under_review", utcnow()
        supplier.status_note = "A required qualification check is no longer satisfied"
        emit_event(db, org_id=p.org_id, entity_type="supplier", entity_id=supplier.id, action="auto_requalification",
                   actor=None, from_state="qualified", to_state="under_review",
                   data={"missing": state.missing_required}, ip=p.ip)
    await db.refresh(check)
    await db.commit()
    return check


def _transition(db: AsyncSession, p: Principal, supplier: Supplier, action: str, to: str, note: str | None) -> None:
    from_state = supplier.status
    supplier.status, supplier.status_note, supplier.updated_at = to, note, utcnow()
    emit_event(db, org_id=p.org_id, entity_type="supplier", entity_id=supplier.id, action=action, actor=p.user.id,
               from_state=from_state, to_state=to, note=note, ip=p.ip)


async def submit_for_qualification(db: AsyncSession, p: Principal, supplier: Supplier) -> Supplier:
    if supplier.status not in ("prospect", "disqualified"):
        raise conflict("invalid_state", f"A supplier that is {supplier.status.replace('_', ' ')} cannot be submitted")
    if not await db.scalar(select(func.count()).select_from(SupplierContact).where(SupplierContact.supplier_id == supplier.id)):
        raise conflict("no_contact", "Add at least one contact before qualification")
    _transition(db, p, supplier, "submitted", "under_review", None)
    supplier.submitted_by, supplier.submitted_at, supplier.decided_by, supplier.decided_at = p.user.id, utcnow(), None, None
    await db.commit()
    return supplier


async def qualify(db: AsyncSession, p: Principal, supplier: Supplier, note: str | None) -> Supplier:
    if supplier.status != "under_review":
        raise conflict("invalid_state", "Only suppliers under review can be qualified")
    state = (await load_state(db, p.org_id, [supplier.id]))[supplier.id]
    if state.missing_required:
        raise ApiError(409, "required_checks_missing", "Required checks are not satisfied: " + ", ".join(state.missing_required))
    if supplier.submitted_by == p.user.id and await second_person_required(db, p.org_id):
        raise forbidden("The person who submitted the supplier cannot also qualify it", "segregation_of_duties")
    _transition(db, p, supplier, "qualified", "qualified", note)
    supplier.decided_by, supplier.decided_at = p.user.id, utcnow()
    await db.commit()
    return supplier


async def disqualify(db: AsyncSession, p: Principal, supplier: Supplier, reason: str) -> Supplier:
    if supplier.status not in ("under_review", "qualified"):
        raise conflict("invalid_state", "Only suppliers under review or qualified can be disqualified")
    _transition(db, p, supplier, "disqualified", "disqualified", reason)
    supplier.decided_by, supplier.decided_at = p.user.id, utcnow()
    await db.commit()
    return supplier


async def block(db: AsyncSession, p: Principal, supplier: Supplier, reason: str) -> Supplier:
    if supplier.status == "blocked":
        raise conflict("invalid_state", "This supplier is already blocked")
    _transition(db, p, supplier, "blocked", "blocked", reason)
    supplier.decided_by, supplier.decided_at = p.user.id, utcnow()
    await db.commit()
    return supplier


async def reopen(db: AsyncSession, p: Principal, supplier: Supplier, note: str | None) -> Supplier:
    if supplier.status not in ("disqualified", "blocked"):
        raise conflict("invalid_state", "Only disqualified or blocked suppliers can be reopened")
    _transition(db, p, supplier, "reopened", "prospect", note)
    await db.commit()
    return supplier
