"""Supplier listings, the buyer marketplace and masked RFQ negotiations."""
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import email as mail
from app.db import get_db
from app.deps import Principal, require_permission
from app.errors import not_found
from app.marketplace import profiles, rfq
from app.marketplace import schemas as ms
from app.marketplace import service as svc
from app.marketplace.catalog import REGIONS, zip3
from app.models import LotAnalysis, LotPrivateDetails, MarketplaceLot, MarketProfile, RfqThread
from app.rbac import Permission

router = APIRouter(tags=["marketplace"])
READ = require_permission(Permission.MARKETPLACE_READ)
TRADE = require_permission(Permission.MARKETPLACE_TRADE)
LIST = require_permission(Permission.LISTINGS_MANAGE)


def _send(background: BackgroundTasks, notices: list[rfq.Notice]) -> None:
    for to, subject, body in notices:
        background.add_task(mail.send_email, to, subject, body)


# --- Serialization -------------------------------------------------------------------------------------------
def card(lot: MarketplaceLot, seller: MarketProfile | None) -> dict[str, Any]:
    return {
        "id": lot.id, "title": lot.title, "description": lot.description, "status": lot.status,
        "lot_type": lot.lot_type, "pallet_count": lot.pallet_count, "asking_price": lot.asking_price,
        "currency": lot.currency, "item_count": lot.item_count, "total_units": lot.total_units,
        "total_msrp": lot.total_msrp, "est_resale": lot.est_resale, "est_margin_pct": lot.est_margin_pct,
        "primary_category": lot.primary_category, "categories": lot.categories, "condition_mix": lot.condition_mix,
        "category_mix": lot.category_mix, "region": lot.region,
        "ships_from": f"{REGIONS[lot.region]} · ZIP {zip3(lot.dispatch_zip)}" if lot.region else None,
        "liftgate_required": lot.liftgate_required, "dock_high_only": lot.dock_high_only,
        "local_pickup": lot.local_pickup, "published_at": lot.published_at, "seller": profiles.public(seller),
    }


def item(i, supplier: bool = False) -> dict[str, Any]:
    out = {k: getattr(i, k) for k in ("position", "title", "brand", "category", "condition", "upc", "asin",
                                      "model_number", "quantity", "unit_msrp", "unit_price")}
    if supplier:
        out.update(source_line=i.source_line, method=i.method, flags=i.flags)
    return out


async def supplier_lot(db: AsyncSession, lot: MarketplaceLot, doc=None, open_offers: int = 0,
                       items: bool = False, warnings: list[str] | None = None) -> dict[str, Any]:
    details = await db.get(LotPrivateDetails, lot.id)
    status, error = svc.effective_ingest(lot, doc)
    out = {
        "id": lot.id, "source_filename": lot.source_filename, "ingest_status": status, "ingest_method": lot.ingest_method,
        "ingest_error": error, "ingest_flags": lot.ingest_flags, "status": lot.status, "title": lot.title,
        "description": lot.description, "lot_type": lot.lot_type, "pallet_count": lot.pallet_count,
        "asking_price": lot.asking_price, "reserve_price": details.reserve_price if details else None,
        "dispatch_zip": lot.dispatch_zip, "region": lot.region, "liftgate_required": lot.liftgate_required,
        "dock_high_only": lot.dock_high_only, "local_pickup": lot.local_pickup,
        **{k: getattr(details, k) if details else None for k in ("pickup_address", "pickup_contact", "pickup_phone", "pickup_notes")},
        "item_count": lot.item_count, "total_units": lot.total_units, "total_msrp": lot.total_msrp,
        "est_resale": lot.est_resale, "est_margin_pct": lot.est_margin_pct, "condition_mix": lot.condition_mix,
        "category_mix": lot.category_mix, "open_offers": open_offers,
        "publish_problems": svc.publish_problems(lot) if lot.status in ("draft", "withdrawn") else [],
        "created_at": lot.created_at, "published_at": lot.published_at,
    }
    if items:
        out["items"] = [item(i, supplier=True) for i in await svc.items_of(db, lot.id)]
        out["warnings"] = warnings or []
    return out


async def thread_out(db: AsyncSession, p: Principal, thread: RfqThread, *, full: bool,
                     warnings: list[str] | None = None) -> dict[str, Any]:
    side = rfq.side_of(thread, p.org_id)
    other_org = thread.supplier_org_id if side == "buyer" else thread.buyer_org_id
    counterparty = await db.get(MarketProfile, other_org)
    awaiting = None
    if thread.status == "open" and thread.offer_by:
        awaiting = "them" if thread.offer_by == side else "you"
    out: dict[str, Any] = {
        "id": thread.id, "lot_id": thread.lot_id, "lot_title": thread.lot_title, "my_side": side,
        "counterparty": profiles.public(counterparty), "status": thread.status, "closed_reason": thread.closed_reason,
        "offer_by": thread.offer_by, "offer_price": thread.offer_price, "agreed_price": thread.agreed_price,
        "awaiting": awaiting, "last_event_at": thread.last_event_at,
    }
    if full:
        events = await rfq.events_of(db, thread.id)
        mine = await db.get(MarketProfile, p.org_id)
        names = {side: mine.alias if mine else "You", rfq.other(side): counterparty.alias if counterparty else "Them",
                 "system": "Marketplace"}
        out.update(
            shipping_terms=thread.shipping_terms, target_close_date=thread.target_close_date,
            custom_terms=thread.custom_terms, revealed=thread.revealed if thread.status == "accepted" else None,
            events=[{"id": e.id, "kind": e.kind, "side": e.actor_side, "mine": e.actor_side == side,
                     "author": names.get(e.actor_side, e.actor_side), "offer": e.offer, "body": e.body,
                     "flags": e.flags, "created_at": e.created_at} for e in events],
            chain_valid=rfq.chain_valid(events), chain_head=events[-1].hash if events else None,
            warnings=warnings or [],
        )
    return out


# --- Supplier side --------------------------------------------------------------------------------------------
@router.post("/supplier/manifests", response_model=ms.SupplierLotOut, status_code=201)
async def upload_manifest(
    file: UploadFile = File(...), default_condition: ms.Condition | None = Form(default=None),
    p: Principal = Depends(LIST), db: AsyncSession = Depends(get_db),
):
    lot = await svc.upload_manifest(db, p, file, default_condition)
    return await supplier_lot(db, lot)


@router.get("/supplier/lots", response_model=list[ms.SupplierLotOut])
async def list_lots(p: Principal = Depends(READ), db: AsyncSession = Depends(get_db)):
    return [await supplier_lot(db, lot, doc, n) for lot, doc, n in await svc.list_own_lots(db, p)]


@router.get("/supplier/lots/{lot_id}", response_model=ms.SupplierLotDetailOut)
async def get_own_lot(lot_id: uuid.UUID, p: Principal = Depends(READ), db: AsyncSession = Depends(get_db)):
    lot = await svc.own_lot(db, p, lot_id)
    rows = [r for r in await svc.list_own_lots(db, p) if r[0].id == lot.id]
    _, doc, n = rows[0]
    return await supplier_lot(db, lot, doc, n, items=True)


@router.patch("/supplier/lots/{lot_id}", response_model=ms.SupplierLotDetailOut)
async def update_lot(lot_id: uuid.UUID, body: ms.LotUpdateIn, p: Principal = Depends(LIST),
                     db: AsyncSession = Depends(get_db)):
    lot, warnings = await svc.update_lot(db, p, lot_id, body.model_dump(exclude_unset=True))
    return await supplier_lot(db, lot, items=True, warnings=warnings)


@router.post("/supplier/lots/{lot_id}/publish", response_model=ms.SupplierLotDetailOut)
async def publish_lot(lot_id: uuid.UUID, p: Principal = Depends(LIST), db: AsyncSession = Depends(get_db)):
    return await supplier_lot(db, await svc.publish(db, p, lot_id), items=True)


@router.post("/supplier/lots/{lot_id}/withdraw", response_model=ms.SupplierLotDetailOut)
async def withdraw_lot(lot_id: uuid.UUID, background: BackgroundTasks, p: Principal = Depends(LIST),
                       db: AsyncSession = Depends(get_db)):
    lot, notices = await svc.withdraw(db, p, lot_id)
    _send(background, notices)
    return await supplier_lot(db, lot, items=True)


# --- Buyer side -----------------------------------------------------------------------------------------------
@router.get("/marketplace/lots", response_model=ms.MarketplaceOut)
async def marketplace(
    category: list[str] = Query(default=[]), condition: list[str] = Query(default=[]),
    lot_type: list[str] = Query(default=[]), region: list[str] = Query(default=[]),
    min_margin: float | None = None, max_price: float | None = None, q: str | None = Query(default=None, max_length=100),
    sort: str = "newest", limit: int = Query(default=24, ge=1, le=100), offset: int = Query(default=0, ge=0),
    p: Principal = Depends(READ), db: AsyncSession = Depends(get_db),
):
    lots, total, sellers = await svc.search(
        db, p, categories=category, conditions=condition, lot_types=lot_type, regions=region, min_margin=min_margin,
        max_price=max_price, q=q, sort=sort, limit=limit, offset=offset,
    )
    return {"total": total, "lots": [card(lot, sellers.get(lot.organization_id)) for lot in lots]}


def analysis_out(a: LotAnalysis) -> dict[str, Any]:
    return {k: getattr(a, k) for k in ("id", "lot_id", "status", "params", "method", "result", "error", "created_at",
                                       "completed_at")}


@router.get("/marketplace/lots/{lot_id}", response_model=ms.LotDetailOut)
async def marketplace_lot(lot_id: uuid.UUID, p: Principal = Depends(READ), db: AsyncSession = Depends(get_db)):
    svc.require_buyer(p)
    lot = await svc.listed_lot(db, lot_id)
    sellers = await svc.profiles_for(db, [lot.organization_id])
    analyses = (await db.scalars(select(LotAnalysis).where(
        LotAnalysis.organization_id == p.org_id, LotAnalysis.lot_id == lot.id
    ).order_by(LotAnalysis.created_at.desc()).limit(10))).all()
    open_thread = await db.scalar(select(RfqThread.id).where(
        RfqThread.lot_id == lot.id, RfqThread.buyer_org_id == p.org_id, RfqThread.status == "open"
    ))
    return {
        **card(lot, sellers.get(lot.organization_id)),
        "items": [item(i) for i in await svc.items_of(db, lot.id)],
        "analyses": [analysis_out(a) for a in analyses], "open_thread_id": open_thread,
        "credit_balance": p.org.credit_balance, "ai_access": svc.access_summary(p)["ai_access"], "analysis_cost": 1,
    }


@router.post("/marketplace/lots/{lot_id}/analyses", response_model=ms.AnalysisOut, status_code=202)
async def run_analysis(lot_id: uuid.UUID, body: ms.AnalysisIn, p: Principal = Depends(TRADE),
                       db: AsyncSession = Depends(get_db)):
    return analysis_out(await svc.request_analysis(db, p, lot_id, body.model_dump()))


@router.get("/marketplace/analyses/{analysis_id}", response_model=ms.AnalysisOut)
async def get_analysis(analysis_id: uuid.UUID, p: Principal = Depends(READ), db: AsyncSession = Depends(get_db)):
    analysis = await db.get(LotAnalysis, analysis_id)
    if analysis is None or analysis.organization_id != p.org_id:
        raise not_found("Analysis not found")
    return analysis_out(analysis)


@router.post("/marketplace/lots/{lot_id}/offers", response_model=ms.ThreadOut, status_code=201)
async def submit_offer(lot_id: uuid.UUID, body: ms.OfferIn, background: BackgroundTasks,
                       p: Principal = Depends(TRADE), db: AsyncSession = Depends(get_db)):
    thread, warnings, notices = await rfq.create_offer(
        db, p, lot_id, price=body.offered_price, shipping_terms=body.shipping_terms,
        target_close_date=body.target_close_date, custom_terms=body.custom_terms,
    )
    _send(background, notices)
    return await thread_out(db, p, thread, full=True, warnings=warnings)


# --- Negotiations ---------------------------------------------------------------------------------------------
@router.get("/rfq-threads", response_model=list[ms.ThreadSummaryOut])
async def threads(p: Principal = Depends(READ), db: AsyncSession = Depends(get_db)):
    return [await thread_out(db, p, t, full=False) for t in await rfq.list_threads(db, p)]


@router.get("/rfq-threads/{thread_id}", response_model=ms.ThreadOut)
async def thread(thread_id: uuid.UUID, p: Principal = Depends(READ), db: AsyncSession = Depends(get_db)):
    return await thread_out(db, p, await rfq.get_thread(db, p, thread_id), full=True)


@router.post("/rfq-threads/{thread_id}/counter", response_model=ms.ThreadOut)
async def counter(thread_id: uuid.UUID, body: ms.CounterIn, background: BackgroundTasks,
                  p: Principal = Depends(TRADE), db: AsyncSession = Depends(get_db)):
    thread, warnings, notices = await rfq.counter(
        db, p, thread_id, price=body.offered_price, shipping_terms=body.shipping_terms,
        target_close_date=body.target_close_date, custom_terms=body.custom_terms,
    )
    _send(background, notices)
    return await thread_out(db, p, thread, full=True, warnings=warnings)


async def _decide(action: str, thread_id: uuid.UUID, body: ms.DecisionIn, background: BackgroundTasks,
                  p: Principal, db: AsyncSession) -> dict[str, Any]:
    thread, warnings, notices = await rfq.decide(db, p, thread_id, action, body.note)
    _send(background, notices)
    return await thread_out(db, p, thread, full=True, warnings=warnings)


@router.post("/rfq-threads/{thread_id}/accept", response_model=ms.ThreadOut)
async def accept(thread_id: uuid.UUID, background: BackgroundTasks, body: ms.DecisionIn | None = None,
                 p: Principal = Depends(TRADE), db: AsyncSession = Depends(get_db)):
    return await _decide("accept", thread_id, body or ms.DecisionIn(), background, p, db)


@router.post("/rfq-threads/{thread_id}/decline", response_model=ms.ThreadOut)
async def decline(thread_id: uuid.UUID, background: BackgroundTasks, body: ms.DecisionIn | None = None,
                  p: Principal = Depends(TRADE), db: AsyncSession = Depends(get_db)):
    return await _decide("decline", thread_id, body or ms.DecisionIn(), background, p, db)


@router.post("/rfq-threads/{thread_id}/withdraw", response_model=ms.ThreadOut)
async def withdraw(thread_id: uuid.UUID, background: BackgroundTasks, body: ms.DecisionIn | None = None,
                   p: Principal = Depends(TRADE), db: AsyncSession = Depends(get_db)):
    return await _decide("withdraw", thread_id, body or ms.DecisionIn(), background, p, db)


@router.post("/rfq-threads/{thread_id}/messages", response_model=ms.ThreadOut, status_code=201)
async def message(thread_id: uuid.UUID, body: ms.MessageIn, background: BackgroundTasks, response: Response,
                  p: Principal = Depends(TRADE), db: AsyncSession = Depends(get_db)):
    thread, _, warnings, notices = await rfq.send_message(db, p, thread_id, body.body)
    _send(background, notices)
    return await thread_out(db, p, thread, full=True, warnings=warnings)
