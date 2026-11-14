"""Masked RFQ negotiation between a buyer and the seller of a lot.

- Offer -> counteroffer -> ... -> accept / decline / withdraw. Only the party facing an offer can accept,
  decline or counter it; only its author can withdraw it.
- Every step is an append-only, hash-chained event (each hash covers the previous one), so the history can be
  verified and handed to an escrow provider as is.
- Messages and custom terms pass through the contact scrubber; what was removed is recorded as flags, and the
  original is kept for the author's own account only.
- An offer below the seller's private reserve is declined automatically without revealing the reserve.
- Neither side sees the other's company, people or contact details until an offer is accepted. Acceptance
  releases them to both parties, marks the lot sold and closes every other negotiation on it.
"""
import hashlib
import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record
from app.config import get_settings
from app.db import set_system, set_tenant
from app.deps import Principal
from app.errors import ApiError, bad_request, conflict, not_found
from app.marketplace import profiles
from app.marketplace.scrub import scrub
from app.marketplace.service import listed_lot, require_buyer
from app.models import (
    LotPrivateDetails,
    MarketplaceLot,
    MarketProfile,
    Organization,
    RfqModeration,
    RfqThread,
    RfqThreadEvent,
    User,
)

settings = get_settings()
TRADERS = ("admin", "buyer", "approver")
SHIPPING = {"buyer_freight": "Buyer arranges freight", "seller_freight": "Seller arranges freight"}

Notice = tuple[str, str, str]  # (to, subject, body)


def _now() -> datetime:
    return datetime.now(UTC)


def side_of(thread: RfqThread, org_id: uuid.UUID) -> str:
    return "buyer" if thread.buyer_org_id == org_id else "supplier"


def other(side: str) -> str:
    return "supplier" if side == "buyer" else "buyer"


def _money_text(value: Decimal | None) -> str:
    return f"${value:,.2f}" if value is not None else "-"


# --- Event log ---------------------------------------------------------------------------------------------
def _event_hash(prev: str | None, payload: dict[str, Any]) -> str:
    return hashlib.sha256(((prev or "") + json.dumps(payload, sort_keys=True, default=str)).encode()).hexdigest()


def _payload(event: RfqThreadEvent) -> dict[str, Any]:
    return {
        "thread": str(event.thread_id), "side": event.actor_side, "user": str(event.actor_user_id or ""),
        "kind": event.kind, "offer": event.offer, "body": event.body, "flags": event.flags,
        "at": event.created_at.isoformat(),
    }


async def _append(db: AsyncSession, thread: RfqThread, side: str, user_id: uuid.UUID | None, kind: str, *,
                  offer: dict[str, Any] | None = None, body: str | None = None,
                  flags: list[str] | None = None) -> RfqThreadEvent:
    prev = await db.scalar(
        select(RfqThreadEvent.hash).where(RfqThreadEvent.thread_id == thread.id).order_by(RfqThreadEvent.id.desc()).limit(1)
    )
    event = RfqThreadEvent(
        thread_id=thread.id, buyer_org_id=thread.buyer_org_id, supplier_org_id=thread.supplier_org_id,
        actor_side=side, actor_user_id=user_id, kind=kind, offer=offer, body=body, flags=flags or [],
        prev_hash=prev, created_at=_now(),
    )
    event.hash = _event_hash(prev, _payload(event))
    db.add(event)
    await db.flush()
    thread.last_event_at = event.created_at
    return event


def chain_valid(events: list[RfqThreadEvent]) -> bool:
    prev = None
    for event in events:
        if event.prev_hash != prev or event.hash != _event_hash(prev, _payload(event)):
            return False
        prev = event.hash
    return True


async def events_of(db: AsyncSession, thread_id: uuid.UUID) -> list[RfqThreadEvent]:
    return list((await db.scalars(
        select(RfqThreadEvent).where(RfqThreadEvent.thread_id == thread_id).order_by(RfqThreadEvent.id)
    )).all())


# --- Cross-account helpers (system context, always restored) --------------------------------------------------
async def _as_system(db: AsyncSession, restore_org: uuid.UUID, fn):
    await set_system(db)
    try:
        return await fn()
    finally:
        await set_tenant(db, restore_org)


async def _recipients(db: AsyncSession, org_id: uuid.UUID, restore_org: uuid.UUID) -> list[str]:
    async def run():
        rows = await db.scalars(select(User.email).where(
            User.organization_id == org_id, User.is_active.is_(True), User.role.in_(TRADERS)
        ))
        return list(rows)
    return await _as_system(db, restore_org, run)


async def _reserve(db: AsyncSession, lot_id: uuid.UUID, restore_org: uuid.UUID) -> Decimal | None:
    async def run():
        details = await db.get(LotPrivateDetails, lot_id)
        return details.reserve_price if details else None
    return await _as_system(db, restore_org, run)


def _notice(to: list[str], subject: str, lines: list[str], thread: RfqThread) -> list[Notice]:
    body = "\n".join(lines) + (
        f"\n\nOpen the conversation: {settings.app_base_url}/rfq/{thread.id}\n\n"
        "Identities and contact details stay private until an offer is accepted. Keep the negotiation and payment "
        "on the platform: offers made here are recorded and protected.\n"
    )
    return [(addr, subject, body) for addr in to]


async def _notify(db: AsyncSession, thread: RfqThread, to_side: str, subject: str, lines: list[str],
                  restore_org: uuid.UUID) -> list[Notice]:
    org_id = thread.buyer_org_id if to_side == "buyer" else thread.supplier_org_id
    return _notice(await _recipients(db, org_id, restore_org), subject, lines, thread)


async def _alias(db: AsyncSession, org_id: uuid.UUID) -> MarketProfile | None:
    return await db.get(MarketProfile, org_id)


def _describe(profile: MarketProfile | None) -> str:
    if profile is None:
        return "A marketplace member"
    return f"{profile.alias} ({', '.join(profiles.badges(profile))})"


# --- Offers -------------------------------------------------------------------------------------------------
def _offer(price: Decimal, shipping: str | None, close: date, terms: str | None) -> dict[str, Any]:
    return {"price": str(price), "shipping_terms": shipping, "target_close_date": close.isoformat(), "custom_terms": terms}


def _validate_offer(price: Decimal, shipping: str, close: date) -> None:
    if price <= 0:
        raise bad_request("invalid_price", "Enter an offer above zero")
    if shipping not in SHIPPING:
        raise bad_request("invalid_shipping", "Choose who arranges freight")
    if close < date.today():
        raise bad_request("invalid_close_date", "The target close date cannot be in the past")


async def _moderate(db: AsyncSession, p: Principal, thread: RfqThread, event: RfqThreadEvent, original: str,
                    flags: list[str]) -> None:
    if any(f != "payment_off_platform" for f in flags):
        db.add(RfqModeration(organization_id=p.org_id, thread_id=thread.id, event_id=event.id, author_id=p.user.id,
                             original=original, flags=flags))


async def create_offer(db: AsyncSession, p: Principal, lot_id: uuid.UUID, *, price: Decimal, shipping_terms: str,
                       target_close_date: date, custom_terms: str | None) -> tuple[RfqThread, list[str], list[Notice]]:
    require_buyer(p)
    lot = await listed_lot(db, lot_id, published_only=True)
    _validate_offer(price, shipping_terms, target_close_date)
    existing = await db.scalar(select(RfqThread.id).where(
        RfqThread.lot_id == lot.id, RfqThread.buyer_org_id == p.org_id, RfqThread.status == "open"
    ))
    if existing is not None:
        raise ApiError(409, "offer_open", "You already have an open negotiation on this lot",
                       extra={"thread_id": str(existing)})
    cleaned = scrub(custom_terms.strip(), names=(p.org.name,)) if custom_terms and custom_terms.strip() else None
    buyer_profile = await profiles.refresh(db, p.org_id, restore_org=p.org_id)
    thread = RfqThread(
        lot_id=lot.id, lot_title=lot.title, buyer_org_id=p.org_id, supplier_org_id=lot.organization_id,
        created_by=p.user.id, status="open", offer_by="buyer", offer_price=price, shipping_terms=shipping_terms,
        target_close_date=target_close_date, custom_terms=cleaned.text if cleaned else None,
    )
    db.add(thread)
    await db.flush()
    flags = cleaned.flags if cleaned else []
    event = await _append(db, thread, "buyer", p.user.id, "offer",
                          offer=_offer(price, shipping_terms, target_close_date, thread.custom_terms), flags=flags)
    if cleaned:
        await _moderate(db, p, thread, event, custom_terms or "", flags)
    warnings = cleaned.warnings if cleaned else []
    notices: list[Notice] = []
    reserve = await _reserve(db, lot.id, p.org_id)
    if reserve is not None and price < reserve:
        await _append(db, thread, "system", None, "auto_decline",
                      body="Declined automatically: the offer is below the seller's minimum.")
        thread.status, thread.closed_reason, thread.offer_by = "declined", "below_reserve", None
        warnings.append("This seller automatically declines offers below their minimum, so your offer was not sent. "
                        "You can make a new offer.")
    else:
        notices = await _notify(db, thread, "supplier", f"New offer on your lot: {lot.title}", [
            f"{_describe(buyer_profile)} offered {_money_text(price)} for \"{lot.title}\".",
            f"Freight: {SHIPPING[shipping_terms]}. Target close date: {target_close_date.isoformat()}.",
            *([f"Terms: {thread.custom_terms}"] if thread.custom_terms else []),
        ], p.org_id)
    record(db, org_id=p.org_id, user_id=p.user.id, action="rfq.offer_submitted", ip=p.ip,
           meta={"thread_id": str(thread.id), "lot_id": str(lot.id), "price": str(price), "status": thread.status})
    await db.commit()
    return thread, warnings, notices


async def _locked_thread(db: AsyncSession, p: Principal, thread_id: uuid.UUID) -> RfqThread:
    thread = await db.scalar(
        select(RfqThread).where(RfqThread.id == thread_id).with_for_update().execution_options(populate_existing=True)
    )
    if thread is None or p.org_id not in (thread.buyer_org_id, thread.supplier_org_id):
        raise not_found("Conversation not found")
    return thread


async def get_thread(db: AsyncSession, p: Principal, thread_id: uuid.UUID) -> RfqThread:
    thread = await db.get(RfqThread, thread_id)
    if thread is None or p.org_id not in (thread.buyer_org_id, thread.supplier_org_id):
        raise not_found("Conversation not found")
    return thread


def _require_open(thread: RfqThread) -> None:
    if thread.status != "open":
        raise conflict("thread_closed", f"This negotiation is {thread.status}")


async def counter(db: AsyncSession, p: Principal, thread_id: uuid.UUID, *, price: Decimal, shipping_terms: str | None,
                  target_close_date: date | None, custom_terms: str | None) -> tuple[RfqThread, list[str], list[Notice]]:
    thread = await _locked_thread(db, p, thread_id)
    side = side_of(thread, p.org_id)
    _require_open(thread)
    if thread.offer_by == side:
        raise conflict("awaiting_response", "Wait for the other party to answer your offer, or withdraw it")
    shipping_terms = shipping_terms or thread.shipping_terms or "buyer_freight"
    target_close_date = target_close_date or thread.target_close_date or date.today()
    _validate_offer(price, shipping_terms, target_close_date)
    cleaned = scrub(custom_terms.strip(), names=(p.org.name,)) if custom_terms and custom_terms.strip() else None
    terms = cleaned.text if cleaned else thread.custom_terms
    flags = cleaned.flags if cleaned else []
    event = await _append(db, thread, side, p.user.id, "counter",
                          offer=_offer(price, shipping_terms, target_close_date, terms), flags=flags)
    if cleaned:
        await _moderate(db, p, thread, event, custom_terms or "", flags)
    warnings = cleaned.warnings if cleaned else []
    thread.offer_by, thread.offer_price, thread.shipping_terms = side, price, shipping_terms
    thread.target_close_date, thread.custom_terms = target_close_date, terms
    notices: list[Notice] = []
    reserve = await _reserve(db, thread.lot_id, p.org_id) if side == "buyer" and thread.lot_id else None
    if reserve is not None and price < reserve:
        await _append(db, thread, "system", None, "auto_decline",
                      body="Declined automatically: the offer is below the seller's minimum.")
        thread.status, thread.closed_reason, thread.offer_by = "declined", "below_reserve", None
        warnings.append("This seller automatically declines offers below their minimum, so your counteroffer was not sent.")
    else:
        me = await _alias(db, p.org_id)
        notices = await _notify(db, thread, other(side), f"Counteroffer on {thread.lot_title}", [
            f"{_describe(me)} countered with {_money_text(price)} for \"{thread.lot_title}\".",
            f"Freight: {SHIPPING[shipping_terms]}. Target close date: {target_close_date.isoformat()}.",
        ], p.org_id)
    record(db, org_id=p.org_id, user_id=p.user.id, action="rfq.countered", ip=p.ip,
           meta={"thread_id": str(thread.id), "price": str(price)})
    await db.commit()
    return thread, warnings, notices


async def close_open_threads(db: AsyncSession, lot: MarketplaceLot, *, reason: str, restore_org: uuid.UUID | None = None,
                             except_id: uuid.UUID | None = None) -> list[Notice]:
    """Close every other open negotiation on a lot (sold or withdrawn) and tell those buyers."""
    restore = restore_org or lot.organization_id
    message = {"lot_sold": "This lot was sold to another buyer.", "lot_withdrawn": "The seller withdrew this lot."}[reason]
    await set_system(db)
    try:
        threads = (await db.scalars(select(RfqThread).where(
            RfqThread.lot_id == lot.id, RfqThread.status == "open",
            *( [RfqThread.id != except_id] if except_id else [] ),
        ).with_for_update())).all()
        notices: list[Notice] = []
        for thread in threads:
            await _append(db, thread, "system", None, "closed", body=message)
            thread.status, thread.closed_reason, thread.offer_by = "closed", reason, None
            emails = list(await db.scalars(select(User.email).where(
                User.organization_id == thread.buyer_org_id, User.is_active.is_(True), User.role.in_(TRADERS)
            )))
            notices += _notice(emails, f"Negotiation closed: {thread.lot_title}", [message], thread)
        return notices
    finally:
        await set_tenant(db, restore)


async def _reveal(db: AsyncSession, thread: RfqThread, restore_org: uuid.UUID) -> dict[str, Any]:
    async def run():
        seller = await db.get(Organization, thread.supplier_org_id)
        buyer = await db.get(Organization, thread.buyer_org_id)
        details = await db.get(LotPrivateDetails, thread.lot_id) if thread.lot_id else None

        async def contact(org_id: uuid.UUID, preferred: uuid.UUID | None) -> dict[str, str] | None:
            user = await db.get(User, preferred) if preferred else None
            if user is None or user.organization_id != org_id:
                user = await db.scalar(select(User).where(
                    User.organization_id == org_id, User.is_active.is_(True), User.role == "admin"
                ).order_by(User.created_at).limit(1))
            return {"name": user.full_name, "email": user.email} if user else None

        return {
            "released_at": _now().isoformat(),
            "seller": {
                "company": seller.name if seller else None,
                "contact": await contact(thread.supplier_org_id, None),
                "pickup_address": details.pickup_address if details else None,
                "pickup_contact": details.pickup_contact if details else None,
                "pickup_phone": details.pickup_phone if details else None,
                "pickup_notes": details.pickup_notes if details else None,
            },
            "buyer": {"company": buyer.name if buyer else None, "contact": await contact(thread.buyer_org_id, thread.created_by)},
        }
    return await _as_system(db, restore_org, run)


async def decide(db: AsyncSession, p: Principal, thread_id: uuid.UUID, action: str,
                 note: str | None = None) -> tuple[RfqThread, list[str], list[Notice]]:
    """accept | decline (the offer facing you) or withdraw (your own offer)."""
    thread = await _locked_thread(db, p, thread_id)
    side = side_of(thread, p.org_id)
    _require_open(thread)
    if action in ("accept", "decline") and thread.offer_by == side:
        raise conflict("own_offer", "You cannot accept or decline your own offer. Withdraw it instead.")
    if action == "withdraw" and thread.offer_by != side:
        raise conflict("not_your_offer", "Only the party that made the current offer can withdraw it")
    warnings: list[str] = []
    body = None
    if note and note.strip():
        cleaned = scrub(note.strip(), names=(p.org.name,))
        body, warnings = cleaned.text, cleaned.warnings
    event = await _append(db, thread, side, p.user.id, action, body=body,
                          offer=_offer(thread.offer_price, thread.shipping_terms, thread.target_close_date,
                                       thread.custom_terms) if action == "accept" else None)
    if note and body != note.strip():
        await _moderate(db, p, thread, event, note, scrub(note.strip(), names=(p.org.name,)).flags)
    me = await _alias(db, p.org_id)
    notices: list[Notice] = []
    if action == "accept":
        thread.status, thread.agreed_price, thread.accepted_at, thread.offer_by = "accepted", thread.offer_price, _now(), None
        if thread.lot_id is not None:  # a quote on a buyer request has no lot to mark sold
            lot = await _as_system(db, p.org_id, lambda: db.get(MarketplaceLot, thread.lot_id))
            await set_system(db)
            try:
                lot.status, lot.sold_at, lot.updated_at = "sold", _now(), _now()
                await db.flush()
            finally:
                await set_tenant(db, p.org_id)
            notices += await close_open_threads(db, lot, reason="lot_sold", restore_org=p.org_id, except_id=thread.id)
        thread.revealed = await _reveal(db, thread, p.org_id)
        for org_id in (thread.buyer_org_id, thread.supplier_org_id):
            await profiles.refresh(db, org_id, restore_org=p.org_id)
        notices += await _notify(db, thread, other(side), f"Offer accepted: {thread.lot_title}", [
            f"{_describe(me)} accepted {_money_text(thread.agreed_price)} for \"{thread.lot_title}\".",
            "Contact and pickup details are now shared in the conversation.",
        ], p.org_id)
    elif action == "decline":
        thread.status, thread.closed_reason, thread.offer_by = "declined", "declined", None
        notices += await _notify(db, thread, other(side), f"Offer declined: {thread.lot_title}", [
            f"{_describe(me)} declined the offer of {_money_text(thread.offer_price)} for \"{thread.lot_title}\".",
        ], p.org_id)
    else:
        thread.status, thread.closed_reason, thread.offer_by = "withdrawn", "withdrawn", None
        notices += await _notify(db, thread, other(side), f"Offer withdrawn: {thread.lot_title}", [
            f"{_describe(me)} withdrew their offer on \"{thread.lot_title}\".",
        ], p.org_id)
    record(db, org_id=p.org_id, user_id=p.user.id, action=f"rfq.{action}ed" if action != "decline" else "rfq.declined",
           ip=p.ip, meta={"thread_id": str(thread.id), "price": str(thread.offer_price)})
    await db.commit()
    return thread, warnings, notices


async def send_message(db: AsyncSession, p: Principal, thread_id: uuid.UUID, text: str) -> tuple[RfqThread, RfqThreadEvent, list[str], list[Notice]]:
    thread = await _locked_thread(db, p, thread_id)
    side = side_of(thread, p.org_id)
    if thread.status not in ("open", "accepted"):
        raise conflict("thread_closed", f"This negotiation is {thread.status}")
    original = text.strip()
    if not original:
        raise bad_request("empty_message", "Write a message first")
    cleaned = scrub(original, names=(p.org.name,))
    event = await _append(db, thread, side, p.user.id, "message", body=cleaned.text, flags=cleaned.flags)
    await _moderate(db, p, thread, event, original, cleaned.flags)
    me = await _alias(db, p.org_id)
    notices = await _notify(db, thread, other(side), f"New message about {thread.lot_title}", [
        f"{_describe(me)} wrote:", "", cleaned.text,
    ], p.org_id)
    record(db, org_id=p.org_id, user_id=p.user.id, action="rfq.message_sent", ip=p.ip,
           meta={"thread_id": str(thread.id), "event_id": event.id, "flags": cleaned.flags})
    await db.commit()
    return thread, event, cleaned.warnings, notices


async def list_threads(db: AsyncSession, p: Principal) -> list[RfqThread]:
    return list((await db.scalars(select(RfqThread).where(
        (RfqThread.buyer_org_id == p.org_id) | (RfqThread.supplier_org_id == p.org_id)
    ).order_by(RfqThread.last_event_at.desc()).limit(200))).all())


async def create_proposal(db: AsyncSession, p: Principal, request_id: uuid.UUID, lot_id: uuid.UUID, *, price: Decimal,
                          shipping_terms: str, target_close_date: date,
                          custom_terms: str | None) -> tuple[RfqThread, list[str], list[Notice]]:
    """A supplier answers a buyer's sourcing request by offering one of its published lots. Opens a masked
    negotiation in which the supplier made the first offer."""
    from app.sourcing.service import lead

    request = await lead(db, p, request_id)
    lot = await db.get(MarketplaceLot, lot_id)
    if lot is None or lot.organization_id != p.org_id:
        raise not_found("Lot not found")
    if lot.status != "published":
        raise conflict("lot_not_published", "Publish the lot before proposing it to a buyer")
    _validate_offer(price, shipping_terms, target_close_date)
    existing = await db.scalar(select(RfqThread.id).where(
        RfqThread.lot_id == lot.id, RfqThread.buyer_org_id == request.organization_id, RfqThread.status == "open"))
    if existing is not None:
        raise ApiError(409, "offer_open", "There is already an open negotiation with this buyer on this lot",
                       extra={"thread_id": str(existing)})
    cleaned = scrub(custom_terms.strip(), names=(p.org.name,)) if custom_terms and custom_terms.strip() else None
    seller = await profiles.refresh(db, p.org_id, restore_org=p.org_id)
    thread = RfqThread(
        lot_id=lot.id, lot_title=lot.title, buyer_org_id=request.organization_id, supplier_org_id=p.org_id,
        created_by=p.user.id, status="open", offer_by="supplier", offer_price=price, shipping_terms=shipping_terms,
        target_close_date=target_close_date, custom_terms=cleaned.text if cleaned else None, request_id=request.id,
    )
    db.add(thread)
    await db.flush()
    flags = cleaned.flags if cleaned else []
    event = await _append(db, thread, "supplier", p.user.id, "offer",
                          offer=_offer(price, shipping_terms, target_close_date, thread.custom_terms), flags=flags,
                          body=f"Proposed for your request \"{request.title}\".")
    if cleaned:
        await _moderate(db, p, thread, event, custom_terms or "", flags)
    notices = await _notify(db, thread, "buyer", f"A supplier answered your request: {request.title}", [
        f"{_describe(seller)} proposes \"{lot.title}\" for {_money_text(price)}.",
        f"Freight: {SHIPPING[shipping_terms]}. Target close date: {target_close_date.isoformat()}.",
    ], p.org_id)
    record(db, org_id=p.org_id, user_id=p.user.id, action="rfq.proposal_submitted", ip=p.ip,
           meta={"thread_id": str(thread.id), "request_id": str(request.id), "lot_id": str(lot.id), "price": str(price)})
    await db.commit()
    return thread, (cleaned.warnings if cleaned else []), notices
