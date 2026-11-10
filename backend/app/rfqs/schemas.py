import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

Category = Literal["material", "dimensions", "tolerance", "quantity", "delivery", "quality", "compliance",
                   "commercial", "packaging", "other"]
Priority = Literal["mandatory", "preferred", "optional", "unknown"]
CURRENCY = r"^[A-Za-z]{3}$"


def _upper(value: str | None) -> str | None:
    return value.upper() if value else value


class ParamIn(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,59}$")
    value: str | float | None = None
    unit: str | None = Field(default=None, max_length=30)
    qualifier: Literal["exact", "min", "max", "approx"] | None = None
    unknown: bool = False

    @field_validator("value")
    @classmethod
    def strip(cls, value: str | float | None) -> str | float | None:
        return value.strip()[:500] if isinstance(value, str) else value


class RfqCreate(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    from_run_id: uuid.UUID | None = None
    currency: str | None = Field(default=None, pattern=CURRENCY)
    response_due_date: date | None = None
    message: str | None = Field(default=None, max_length=5000)
    category_id: uuid.UUID | None = None

    _c = field_validator("currency")(_upper)


class RfqUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=300)
    currency: str | None = Field(default=None, pattern=CURRENCY)
    response_due_date: date | None = None
    message: str | None = Field(default=None, max_length=5000)
    category_id: uuid.UUID | None = None

    _c = field_validator("currency")(_upper)


class ItemIn(BaseModel):
    statement: str = Field(min_length=3, max_length=2000)
    category: Category = "other"
    priority: Priority = "unknown"
    quantity: float | None = Field(default=None, gt=0)
    unit: str | None = Field(default=None, max_length=30)
    parameters: list[ParamIn] = Field(default_factory=list, max_length=40)


class ItemUpdate(BaseModel):
    statement: str | None = Field(default=None, min_length=3, max_length=2000)
    category: Category | None = None
    priority: Priority | None = None
    quantity: float | None = Field(default=None, gt=0)
    unit: str | None = Field(default=None, max_length=30)
    parameters: list[ParamIn] | None = Field(default=None, max_length=40)
    position: int | None = Field(default=None, ge=0)


class ItemOut(BaseModel):
    id: uuid.UUID
    position: int
    line: int
    kind: str
    statement: str
    category: str
    priority: str
    quantity: float | None
    unit: str | None
    parameters: list[dict[str, Any]]
    source_requirement_id: uuid.UUID | None


class SupplierIdsIn(BaseModel):
    supplier_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)


class RfqSupplierOut(BaseModel):
    supplier_id: uuid.UUID
    code: str
    name: str
    supplier_status: str
    qualified: bool
    contact_count: int
    status: str
    sent_at: datetime | None


class RfqListItem(BaseModel):
    id: uuid.UUID
    number: str
    title: str
    status: str
    version: int
    currency: str
    response_due_date: date | None
    supplier_count: int
    quote_count: int
    created_at: datetime
    updated_at: datetime


class RfqListOut(BaseModel):
    items: list[RfqListItem]
    total: int


class RfqOut(BaseModel):
    id: uuid.UUID
    number: str
    title: str
    status: str
    version: int
    currency: str
    response_due_date: date | None
    message: str | None
    category_id: uuid.UUID | None
    category_name: str | None
    source_run_id: uuid.UUID | None
    content_sha256: str | None
    created_by: uuid.UUID | None
    submitted_by: uuid.UUID | None
    submitted_at: datetime | None
    approved_by: uuid.UUID | None
    approved_at: datetime | None
    sent_at: datetime | None
    closed_at: datetime | None
    decision_note: str | None
    awarded_supplier_id: uuid.UUID | None
    award_comparison_id: uuid.UUID | None
    awarded_by: uuid.UUID | None
    awarded_at: datetime | None
    award_note: str | None
    created_at: datetime
    updated_at: datetime
    items: list[ItemOut]
    suppliers: list[RfqSupplierOut]
    readiness: list[str]


class SuggestionOut(BaseModel):
    supplier_id: uuid.UUID
    code: str
    name: str
    qualification_score: int
    categories: list[str]


class PackageOut(BaseModel):
    id: uuid.UUID
    supplier_id: uuid.UUID
    supplier_name: str
    rfq_version: int
    subject: str
    to_addresses: list[str]
    sha256: str
    size_bytes: int
    manifest: dict[str, Any]
    send_status: str
    sent_at: datetime | None
    send_error: str | None
    created_at: datetime


class QuoteLineIn(BaseModel):
    description: str = Field(min_length=1, max_length=2000)
    quantity: float | None = Field(default=None, ge=0)
    unit: str | None = Field(default=None, max_length=30)
    unit_price: float | None = Field(default=None, ge=0)
    total_price: float | None = Field(default=None, ge=0)
    lead_time_days: float | None = Field(default=None, ge=0, le=3650)
    rfq_item_id: uuid.UUID | None = None


class QuoteHeader(BaseModel):
    currency: str | None = Field(default=None, pattern=CURRENCY)
    incoterms: str | None = Field(default=None, pattern=r"^[A-Za-z]{3}$")
    payment_terms: str | None = Field(default=None, max_length=200)
    valid_until: date | None = None
    notes: str | None = Field(default=None, max_length=4000)

    _c = field_validator("currency", "incoterms")(_upper)


class QuoteIn(QuoteHeader):
    supplier_id: uuid.UUID
    lines: list[QuoteLineIn] = Field(default_factory=list, max_length=300)


class QuoteLinesIn(BaseModel):
    lines: list[QuoteLineIn] = Field(max_length=300)


class QuoteFromDocumentIn(BaseModel):
    supplier_id: uuid.UUID
    document_id: uuid.UUID


class MatchIn(BaseModel):
    rfq_item_id: uuid.UUID | None


class QuoteLineOut(BaseModel):
    id: uuid.UUID
    position: int
    rfq_item_id: uuid.UUID | None
    match_status: str
    match_score: float
    description: str
    quantity: float | None
    unit: str | None
    unit_price: float | None
    total_price: float | None
    lead_time_days: float | None
    confidence: float
    source_quote: str | None


class QuoteOut(BaseModel):
    id: uuid.UUID
    supplier_id: uuid.UUID
    supplier_name: str
    document_id: uuid.UUID | None
    source: str
    status: str
    currency: str | None
    incoterms: str | None
    payment_terms: str | None
    valid_until: date | None
    notes: str | None
    verified_by: uuid.UUID | None
    verified_at: datetime | None
    created_at: datetime
    lines: list[QuoteLineOut]


class CompareIn(BaseModel):
    weights: dict[str, float] | None = None


class ComparisonOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    created_by: uuid.UUID | None
    params: dict[str, Any]
    inputs_sha256: str
    stale: bool
    result: dict[str, Any]


class ComparisonBrief(BaseModel):
    id: uuid.UUID
    created_at: datetime
    created_by: uuid.UUID | None
    weights: dict[str, float]
    highest_score_supplier_id: uuid.UUID | None
    stale: bool


class AwardIn(BaseModel):
    supplier_id: uuid.UUID
    comparison_id: uuid.UUID
    note: str = Field(min_length=10, max_length=4000)
