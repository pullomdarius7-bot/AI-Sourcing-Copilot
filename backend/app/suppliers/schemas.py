import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator


class CategoryIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    parent_id: uuid.UUID | None = None
    description: str | None = Field(default=None, max_length=1000)


class CategoryOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    parent_id: uuid.UUID | None
    description: str | None
    supplier_count: int = 0


class ContactIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=60)
    role: str | None = Field(default=None, max_length=120)
    is_primary: bool = False


class ContactUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=60)
    role: str | None = Field(default=None, max_length=120)
    is_primary: bool | None = None


class ContactOut(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    phone: str | None
    role: str | None
    is_primary: bool


class _SupplierFields(BaseModel):
    legal_name: str | None = Field(default=None, max_length=300)
    country: str | None = Field(default=None, pattern=r"^[A-Za-z]{2}$")
    website: str | None = Field(default=None, max_length=300)
    tax_id: str | None = Field(default=None, max_length=60)
    address: str | None = Field(default=None, max_length=1000)
    notes: str | None = Field(default=None, max_length=4000)
    payment_terms: str | None = Field(default=None, max_length=120)
    currency: str | None = Field(default=None, pattern=r"^[A-Za-z]{3}$")
    typical_lead_time_days: int | None = Field(default=None, ge=0, le=3650)
    tags: list[str] | None = Field(default=None, max_length=30)

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return sorted({t.strip().lower()[:40] for t in value if t.strip()})


class SupplierIn(_SupplierFields):
    name: str = Field(min_length=2, max_length=200)
    category_ids: list[uuid.UUID] = Field(default_factory=list, max_length=30)
    contacts: list[ContactIn] = Field(default_factory=list, max_length=20)


class SupplierUpdate(_SupplierFields):
    name: str | None = Field(default=None, min_length=2, max_length=200)


class CategoryIdsIn(BaseModel):
    category_ids: list[uuid.UUID] = Field(max_length=30)


class QualificationSummary(BaseModel):
    score: int
    ready: bool
    missing_required: list[str]
    expired: list[str]
    qualified: bool  # status is qualified and nothing required is missing or expired


class SupplierOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    legal_name: str | None
    status: str
    country: str | None
    website: str | None
    tax_id: str | None
    address: str | None
    notes: str | None
    payment_terms: str | None
    currency: str | None
    typical_lead_time_days: int | None
    tags: list[str]
    categories: list[CategoryOut]
    contacts: list[ContactOut]
    qualification: QualificationSummary
    submitted_by: uuid.UUID | None
    submitted_at: datetime | None
    decided_by: uuid.UUID | None
    decided_at: datetime | None
    status_note: str | None
    created_at: datetime


class SupplierListItem(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    status: str
    country: str | None
    categories: list[str]
    contact_count: int
    qualification: QualificationSummary


class SupplierListOut(BaseModel):
    items: list[SupplierListItem]
    total: int


class CriterionIn(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,59}$")
    name: str = Field(min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    required: bool = False
    weight: int = Field(default=1, ge=1, le=10)


class CriterionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    required: bool | None = None
    weight: int | None = Field(default=None, ge=1, le=10)
    active: bool | None = None


class CriterionOut(BaseModel):
    id: uuid.UUID
    key: str
    name: str
    description: str | None
    required: bool
    weight: int
    active: bool


class CheckIn(BaseModel):
    status: Literal["pending", "passed", "failed", "waived"]
    note: str | None = Field(default=None, max_length=2000)
    expires_at: date | None = None
    evidence_document_id: uuid.UUID | None = None


class CheckOut(BaseModel):
    criterion: CriterionOut
    status: str
    effective_status: str  # a passed check past its expiry date becomes "expired"
    note: str | None
    expires_at: date | None
    evidence_document_id: uuid.UUID | None
    checked_by: uuid.UUID | None
    checked_at: datetime | None


class QualificationOut(BaseModel):
    supplier_id: uuid.UUID
    status: str
    summary: QualificationSummary
    checks: list[CheckOut]


class NoteIn(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


class EventOut(BaseModel):
    id: int
    action: str
    actor_id: uuid.UUID | None
    actor_email: str | None
    from_state: str | None
    to_state: str | None
    data: dict | None
    note: str | None
    created_at: datetime
