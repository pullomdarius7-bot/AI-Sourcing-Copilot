import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

Condition = Literal["new", "like_new", "customer_return", "salvage"]
LotType = Literal["single_pallet", "multi_pallet", "truckload"]
Shipping = Literal["buyer_freight", "seller_freight"]


class SellerOut(BaseModel):
    alias: str
    label: str
    verified: bool
    badges: list[str]
    completed_deals: int
    member_since: datetime | None


class ItemOut(BaseModel):
    position: int
    title: str
    brand: str | None
    category: str
    condition: str
    upc: str | None
    asin: str | None
    model_number: str | None
    quantity: int
    unit_msrp: Decimal | None
    unit_price: Decimal | None


class SupplierItemOut(ItemOut):
    source_line: int | None
    method: str
    flags: list[str]


class LotCardOut(BaseModel):
    """What every buyer sees. Deliberately has no seller identity, contact, reserve or exact location."""

    id: uuid.UUID
    title: str
    description: str | None
    status: str
    lot_type: str | None
    pallet_count: int | None
    asking_price: Decimal | None
    currency: str
    item_count: int
    total_units: int
    total_msrp: Decimal | None
    est_resale: Decimal | None
    est_margin_pct: Decimal | None
    primary_category: str | None
    categories: list[str]
    condition_mix: dict[str, int]
    category_mix: dict[str, int]
    region: str | None
    ships_from: str | None
    liftgate_required: bool
    dock_high_only: bool
    local_pickup: bool
    published_at: datetime | None
    seller: SellerOut


class MarketplaceOut(BaseModel):
    total: int
    lots: list[LotCardOut]


class AnalysisOut(BaseModel):
    id: uuid.UUID
    lot_id: uuid.UUID
    status: str
    params: dict[str, Any]
    method: str | None
    result: dict[str, Any] | None
    error: str | None
    created_at: datetime
    completed_at: datetime | None


class LotDetailOut(LotCardOut):
    items: list[ItemOut]
    analyses: list[AnalysisOut]
    open_thread_id: uuid.UUID | None
    own: bool
    defaults: dict[str, Any]
    credit_balance: int
    ai_access: bool
    analysis_cost: int


class AnalysisIn(BaseModel):
    """Empty fields use the account's margin rules (Sourcing Team) or the built-in assumptions."""

    channel: Literal["ebay", "amazon", "direct"] | None = None
    target_margin_pct: float | None = Field(default=None, ge=0, le=90)
    freight_cost: float | None = Field(default=None, ge=0, le=1_000_000)
    handling_per_unit: float | None = Field(default=None, ge=0, le=10_000)


class SupplierLotOut(BaseModel):
    id: uuid.UUID
    source_filename: str
    ingest_status: str
    ingest_method: str | None
    ingest_error: str | None
    ingest_flags: list[str]
    status: str
    title: str
    description: str | None
    lot_type: str | None
    pallet_count: int | None
    asking_price: Decimal | None
    reserve_price: Decimal | None
    dispatch_zip: str | None
    region: str | None
    liftgate_required: bool
    dock_high_only: bool
    local_pickup: bool
    pickup_address: str | None
    pickup_contact: str | None
    pickup_phone: str | None
    pickup_notes: str | None
    item_count: int
    total_units: int
    total_msrp: Decimal | None
    est_resale: Decimal | None
    est_margin_pct: Decimal | None
    condition_mix: dict[str, int]
    category_mix: dict[str, int]
    open_offers: int
    publish_problems: list[str]
    created_at: datetime
    published_at: datetime | None


class SupplierLotDetailOut(SupplierLotOut):
    items: list[SupplierItemOut]
    warnings: list[str] = []


class LotUpdateIn(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    lot_type: LotType | None = None
    pallet_count: int | None = Field(default=None, ge=1, le=60)
    asking_price: Decimal | None = Field(default=None, gt=0, le=Decimal("100000000"), decimal_places=2)
    reserve_price: Decimal | None = Field(default=None, gt=0, le=Decimal("100000000"), decimal_places=2)
    dispatch_zip: str | None = Field(default=None, pattern=r"^\d{5}$")
    liftgate_required: bool | None = None
    dock_high_only: bool | None = None
    local_pickup: bool | None = None
    pickup_address: str | None = Field(default=None, max_length=500)
    pickup_contact: str | None = Field(default=None, max_length=200)
    pickup_phone: str | None = Field(default=None, max_length=40)
    pickup_notes: str | None = Field(default=None, max_length=2000)


class OfferIn(BaseModel):
    offered_price: Decimal = Field(gt=0, le=Decimal("100000000"), decimal_places=2)
    shipping_terms: Shipping
    target_close_date: date
    custom_terms: str | None = Field(default=None, max_length=2000)


class CounterIn(BaseModel):
    offered_price: Decimal = Field(gt=0, le=Decimal("100000000"), decimal_places=2)
    shipping_terms: Shipping | None = None
    target_close_date: date | None = None
    custom_terms: str | None = Field(default=None, max_length=2000)


class DecisionIn(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class MessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class EventOut(BaseModel):
    id: int
    kind: str
    side: str
    mine: bool
    author: str
    offer: dict[str, Any] | None
    body: str | None
    flags: list[str]
    created_at: datetime


class ThreadSummaryOut(BaseModel):
    id: uuid.UUID
    lot_id: uuid.UUID | None
    request_id: uuid.UUID | None = None
    lot_title: str
    my_side: Literal["buyer", "supplier"]
    counterparty: SellerOut
    status: str
    closed_reason: str | None
    offer_by: str | None
    offer_price: Decimal | None
    agreed_price: Decimal | None
    awaiting: Literal["you", "them"] | None
    last_event_at: datetime


class ThreadOut(ThreadSummaryOut):
    shipping_terms: str | None
    target_close_date: date | None
    custom_terms: str | None
    revealed: dict[str, Any] | None
    events: list[EventOut]
    chain_valid: bool
    chain_head: str | None
    warnings: list[str] = []
