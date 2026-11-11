"""AI sourcing: buyer requests, found suppliers, the supplier lead feed and invitation links."""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import Principal, require_permission
from app.errors import not_found
from app.marketplace import profiles, rfq
from app.marketplace import schemas as ms
from app.models import MarketProfile, RfqThread, SourcingCandidate, SourcingRequest
from app.ratelimit import auth_rate_limit
from app.rbac import Permission
from app.routers.marketplace import _send, thread_out
from app.sourcing import invites
from app.sourcing import service as svc
from app.sourcing.search import get_search

router = APIRouter(tags=["sourcing"])
READ = require_permission(Permission.MARKETPLACE_READ)
TRADE = require_permission(Permission.MARKETPLACE_TRADE)


class RequestIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    category: str | None = None
    condition: str | None = None
    quantity: int | None = Field(default=None, ge=1, le=10_000_000)
    target_unit_price: Decimal | None = Field(default=None, gt=0, le=Decimal("1000000"), decimal_places=2)
    region: str | None = None
    needed_by: date | None = None
    open_to_suppliers: bool = True
    search_web: Literal["auto", "always", "never"] = "auto"


class CandidateOut(BaseModel):
    id: uuid.UUID
    kind: str
    lot_id: uuid.UUID | None
    company_name: str
    website: str | None
    domain: str | None
    business_type: str | None
    location: str | None
    summary: str | None
    categories: list[str]
    products: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    moq: str | None
    price_notes: str | None
    contact_email: str | None
    contact_phone: str | None
    contact_url: str | None
    relevance: float
    method: str
    status: str
    invite_status: str | None
    supplier_id: uuid.UUID | None
    flags: list[str]


class RequestOut(BaseModel):
    id: uuid.UUID
    title: str
    description: str | None
    category: str | None
    condition: str | None
    quantity: int | None
    target_unit_price: Decimal | None
    region: str | None
    needed_by: date | None
    status: str
    open_to_suppliers: bool
    search_web: str
    web_status: str
    web_note: str | None
    marketplace_matches: int
    web_matches: int
    invites_sent: int
    created_at: datetime
    completed_at: datetime | None


class RequestDetailOut(RequestOut):
    candidates: list[CandidateOut]
    proposals: list[ms.ThreadSummaryOut]
    web_search_available: bool
    web_search_cost: int
    warnings: list[str] = []


class LeadOut(BaseModel):
    id: uuid.UUID
    title: str
    description: str | None
    category: str | None
    condition: str | None
    quantity: int | None
    target_unit_price: Decimal | None
    region: str | None
    needed_by: date | None
    created_at: datetime
    match: float
    buyer: ms.SellerOut
    my_thread_id: uuid.UUID | None


class ProposalIn(BaseModel):
    lot_id: uuid.UUID
    offered_price: Decimal = Field(gt=0, le=Decimal("100000000"), decimal_places=2)
    shipping_terms: ms.Shipping
    target_close_date: date
    custom_terms: str | None = Field(default=None, max_length=2000)


class TokenIn(BaseModel):
    token: str = Field(min_length=10, max_length=100)


def _request(r: SourcingRequest) -> dict[str, Any]:
    return {k: getattr(r, k) for k in RequestOut.model_fields}


def _candidate(c: SourcingCandidate) -> dict[str, Any]:
    return {k: getattr(c, k) for k in CandidateOut.model_fields}


async def _detail(db: AsyncSession, p: Principal, r: SourcingRequest, warnings: list[str] | None = None) -> dict[str, Any]:
    from app.billing.plans import WEB_SOURCING, cost_of

    candidates = (await db.scalars(select(SourcingCandidate).where(SourcingCandidate.request_id == r.id)
                                   .order_by(SourcingCandidate.kind, SourcingCandidate.relevance.desc()))).all()
    threads = (await db.scalars(select(RfqThread).where(RfqThread.request_id == r.id)
                                .order_by(RfqThread.last_event_at.desc()))).all()
    return {
        **_request(r), "candidates": [_candidate(c) for c in candidates if c.status != "dismissed"],
        "proposals": [await thread_out(db, p, t, full=False) for t in threads],
        "web_search_available": get_search() is not None, "web_search_cost": cost_of(WEB_SOURCING),
        "warnings": warnings or [],
    }


@router.post("/sourcing/requests", response_model=RequestDetailOut, status_code=201)
async def create_request(body: RequestIn, p: Principal = Depends(TRADE), db: AsyncSession = Depends(get_db)):
    request, warnings = await svc.create_request(db, p, body.model_dump())
    return await _detail(db, p, request, warnings)


@router.get("/sourcing/requests", response_model=list[RequestOut])
async def list_requests(p: Principal = Depends(READ), db: AsyncSession = Depends(get_db)):
    svc.require_buyer(p)
    rows = await db.scalars(select(SourcingRequest).where(SourcingRequest.organization_id == p.org_id)
                            .order_by(SourcingRequest.created_at.desc()).limit(200))
    return [_request(r) for r in rows]


@router.get("/sourcing/requests/{request_id}", response_model=RequestDetailOut)
async def get_request(request_id: uuid.UUID, p: Principal = Depends(READ), db: AsyncSession = Depends(get_db)):
    return await _detail(db, p, await svc.own_request(db, p, request_id))


@router.post("/sourcing/requests/{request_id}/search-web", response_model=RequestDetailOut, status_code=202)
async def search_web(request_id: uuid.UUID, p: Principal = Depends(TRADE), db: AsyncSession = Depends(get_db)):
    return await _detail(db, p, await svc.search_web_now(db, p, request_id))


@router.post("/sourcing/requests/{request_id}/close", response_model=RequestDetailOut)
async def close_request(request_id: uuid.UUID, p: Principal = Depends(TRADE), db: AsyncSession = Depends(get_db)):
    from datetime import UTC

    request = await svc.own_request(db, p, request_id)
    request.status, request.closed_at = "closed", datetime.now(UTC)
    await db.commit()
    return await _detail(db, p, request)


@router.post("/sourcing/candidates/{candidate_id}/add-to-suppliers", response_model=CandidateOut)
async def add_candidate(candidate_id: uuid.UUID,
                        p: Principal = Depends(require_permission(Permission.MARKETPLACE_TRADE, Permission.SUPPLIERS_MANAGE)),
                        db: AsyncSession = Depends(get_db)):
    return _candidate(await svc.add_to_directory(db, p, candidate_id))


@router.post("/sourcing/candidates/{candidate_id}/dismiss", response_model=CandidateOut)
async def dismiss_candidate(candidate_id: uuid.UUID, p: Principal = Depends(TRADE), db: AsyncSession = Depends(get_db)):
    candidate = await svc.own_candidate(db, p, candidate_id)
    candidate.status = "dismissed"
    await db.commit()
    return _candidate(candidate)


# --- Supplier lead feed ---------------------------------------------------------------------------------------
@router.get("/supplier/requests", response_model=list[LeadOut])
async def supplier_leads(p: Principal = Depends(READ), db: AsyncSession = Depends(get_db)):
    rows = await svc.leads(db, p)
    buyers = {pr.organization_id: pr for pr in (await db.scalars(select(MarketProfile).where(
        MarketProfile.organization_id.in_({r.organization_id for r, _ in rows})))).all()} if rows else {}
    mine = dict((await db.execute(select(RfqThread.request_id, RfqThread.id).where(
        RfqThread.supplier_org_id == p.org_id, RfqThread.request_id.is_not(None)))).all())
    return [{**{k: getattr(r, k) for k in ("id", "title", "description", "category", "condition", "quantity",
                                           "target_unit_price", "region", "needed_by", "created_at")},
             "match": score, "buyer": profiles.public(buyers.get(r.organization_id)), "my_thread_id": mine.get(r.id)}
            for r, score in rows]


@router.post("/supplier/requests/{request_id}/proposals", response_model=ms.ThreadOut, status_code=201)
async def propose(request_id: uuid.UUID, body: ProposalIn, background: BackgroundTasks, p: Principal = Depends(TRADE),
                  db: AsyncSession = Depends(get_db)):
    thread, warnings, notices = await rfq.create_proposal(
        db, p, request_id, body.lot_id, price=body.offered_price, shipping_terms=body.shipping_terms,
        target_close_date=body.target_close_date, custom_terms=body.custom_terms,
    )
    _send(background, notices)
    return await thread_out(db, p, thread, full=True, warnings=warnings)


# --- Invitation links (public) -----------------------------------------------------------------------------------
@router.get("/invites/preview", dependencies=[Depends(auth_rate_limit)])
async def invite_preview(token: str = Query(min_length=10, max_length=100), db: AsyncSession = Depends(get_db)):
    preview = await invites.invite_preview(db, token)
    if preview is None:
        raise not_found("This invitation is no longer valid")
    return preview


@router.post("/invites/unsubscribe", dependencies=[Depends(auth_rate_limit)])
async def unsubscribe(body: TokenIn, db: AsyncSession = Depends(get_db)):
    invite = await invites.unsubscribe(db, body.token)
    if invite is None:
        raise not_found("This link is not valid")
    return {"unsubscribed": True, "domain": invite.domain}

