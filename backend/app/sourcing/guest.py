"""Free guest quotes from invited suppliers, and the masking of web-found suppliers.

An invited supplier opens the link in its invitation and quotes on the buyer requests it was invited to, without
an account and at no cost. The buyer sees the quote under a masked alias and can decline it or say it wants to go
ahead (with a reply). Going ahead needs the supplier to register (free, 75% off the first paid month): on
registration every open quote becomes a normal masked negotiation with the buyer's reply in it.

Masking: the buyer never sees a web supplier's business name, website, email or phone. Those stay in the
platform-only invitation record; what the buyer gets is an alias and a profile scrubbed of names and contacts.
"""
import base64
import hashlib
import hmac
import re
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import set_system, set_tenant
from app.deps import Principal
from app.errors import bad_request, conflict, not_found
from app.marketplace.catalog import CONDITIONS
from app.marketplace.scrub import scrub
from app.models import GuestQuote, RfqThread, SourcingCandidate, SourcingRequest, SupplierInvite, User
from app.sourcing import invites

settings = get_settings()


def _now() -> datetime:
    return datetime.now(UTC)


def domain_hash(domain: str) -> str:
    return hashlib.sha256(domain.lower().encode()).hexdigest()


def masked_alias(domain: str) -> str:
    digest = hmac.new(settings.jwt_secret.encode(), f"web-supplier:{domain.lower()}".encode(), hashlib.sha256).digest()
    return f"Supplier W-{base64.b32encode(digest).decode()[:5]}"


def _names(company: str, domain: str) -> tuple[str, ...]:
    label = domain.split(".")[0]
    return tuple(n for n in {company, domain, label, label.replace("-", " ")} if len(n) >= 4)


def masked_text(text: str | None, company: str, domain: str) -> str | None:
    if not text:
        return text
    out = scrub(text, names=_names(company, domain)).text
    out = re.sub(r"^\[name removed\]", "This supplier", out)  # read naturally instead of showing a marker
    return out.replace("[name removed]", "the supplier")


def coarse_location(location: str | None) -> str | None:
    """'2200 S Ashland Ave, Chicago, IL' -> 'IL': a region, never an address or a city that identifies the firm."""
    if not location:
        return None
    parts = [p.strip() for p in location.split(",") if p.strip()]
    last = parts[-1] if parts else ""
    state = re.match(r"^([A-Z]{2})\b", last)
    return state.group(1) if state else (last[:40] if len(parts) > 1 else None)


def masked_profile(profile) -> dict[str, Any]:
    """The buyer-facing fields of a web-found supplier."""
    company, domain = profile.company_name, profile.domain
    return {
        "company_name": masked_alias(domain), "domain_hash": domain_hash(domain),
        "website": None, "domain": None, "contact_email": None, "contact_phone": None, "contact_url": None,
        "location": coarse_location(profile.location), "summary": masked_text(profile.summary, company, domain),
        "products": [{"name": masked_text(p["name"], company, domain), "quote": masked_text(p["quote"], company, domain)}
                     for p in profile.products],
        "evidence": [{"quote": masked_text(e["quote"], company, domain)} for e in profile.evidence],
        "moq": masked_text(profile.moq, company, domain), "price_notes": masked_text(profile.price_notes, company, domain),
    }


# --- The supplier's side (no account) ------------------------------------------------------------------------------
async def _invite(db: AsyncSession, token: str) -> SupplierInvite:
    invite = await db.scalar(select(SupplierInvite).where(SupplierInvite.token_hash == invites._hash(token)).with_for_update())
    if invite is None or invite.status == "suppressed":
        raise not_found("This invitation is no longer valid")
    return invite


def _request_out(r: SourcingRequest) -> dict[str, Any]:
    return {k: getattr(r, k) for k in ("id", "title", "description", "category", "condition", "quantity",
                                       "target_unit_price", "region", "needed_by")}


async def quote_form(db: AsyncSession, token: str) -> dict[str, Any]:
    await set_system(db)
    try:
        invite = await _invite(db, token)
        ids = [uuid.UUID(d["request_id"]) for d in invite.demand if d.get("request_id")]
        requests = (await db.scalars(select(SourcingRequest).where(
            SourcingRequest.id.in_(ids), SourcingRequest.status == "open", SourcingRequest.open_to_suppliers.is_(True)
        ))).all() if ids else []
        quotes = {q.request_id: q for q in (await db.scalars(select(GuestQuote).where(GuestQuote.invite_id == invite.id))).all()}
        return {
            "company_name": invite.company_name, "joined": invite.status == "joined",
            "discount_pct": settings.invite_first_month_discount_pct,
            "requests": [{**_request_out(r), "my_quote": None if r.id not in quotes else {
                k: getattr(quotes[r.id], k) for k in ("unit_price", "quantity_available", "condition", "available_by",
                                                     "notes", "status")}} for r in requests],
        }
    finally:
        await db.rollback()


async def submit_quote(db: AsyncSession, token: str, request_id: uuid.UUID, *, unit_price: Decimal,
                       quantity_available: int, condition: str, available_by: date,
                       notes: str | None) -> tuple[GuestQuote, list[str], list[tuple[str, str, str]]]:
    if condition not in CONDITIONS:
        raise bad_request("invalid_condition", "Choose the condition of the goods")
    if available_by < date.today():
        raise bad_request("invalid_date", "The availability date cannot be in the past")
    await set_system(db)
    invite = await _invite(db, token)
    if invite.status == "joined":
        raise conflict("already_registered", "Your business is registered: sign in to answer buyers")
    if all(d.get("request_id") != str(request_id) for d in invite.demand):
        raise not_found("This request was not part of your invitation")
    request = await db.get(SourcingRequest, request_id)
    if request is None or request.status != "open" or not request.open_to_suppliers:
        raise conflict("request_closed", "The buyer has closed this request")
    cleaned = scrub(notes.strip(), names=_names(invite.company_name, invite.domain)) if notes and notes.strip() else None
    quote = await db.scalar(select(GuestQuote).where(GuestQuote.invite_id == invite.id, GuestQuote.request_id == request_id))
    if quote is not None and quote.status in ("converted", "declined"):
        raise conflict("quote_closed", "This quote has already been answered")
    first = quote is None
    if quote is None:
        quote = GuestQuote(organization_id=request.organization_id, request_id=request_id, invite_id=invite.id,
                           supplier_alias=masked_alias(invite.domain))
        db.add(quote)
    quote.unit_price, quote.quantity_available, quote.condition = unit_price, quantity_available, condition
    quote.available_by, quote.notes = available_by, cleaned.text if cleaned else None
    quote.flags, quote.status, quote.updated_at = (cleaned.flags if cleaned else []), "new", _now()
    candidate = await db.scalar(select(SourcingCandidate).where(
        SourcingCandidate.request_id == request_id, SourcingCandidate.invite_id == invite.id))
    if candidate is not None:
        candidate.invite_status = "quoted"
    creator = await db.get(User, request.created_by) if request.created_by else None
    await db.commit()
    notices = []
    if creator is not None:
        notices.append((creator.email, f"{'New' if first else 'Updated'} quote for {request.title}", (
            f"{quote.supplier_alias}, a supplier our AI sourcing engine found for you, quoted "
            f"{quantity_available:,} units ({CONDITIONS[condition]}) at ${unit_price:,.2f} each, available by "
            f"{available_by.isoformat()}.\n\nReview it: {settings.app_base_url}/sourcing/{request.id}\n\n"
            "The supplier's identity stays private; reply on the platform to go ahead.\n")))
    return quote, (cleaned.warnings if cleaned else []), notices


# --- The buyer's side --------------------------------------------------------------------------------------------
async def respond(db: AsyncSession, p: Principal, quote_id: uuid.UUID, action: str,
                  message: str | None) -> tuple[GuestQuote, list[str], list[tuple[str, str, str]]]:
    quote = await db.get(GuestQuote, quote_id)
    if quote is None or quote.organization_id != p.org_id:
        raise not_found("Quote not found")
    if quote.status not in ("new", "interested"):
        raise conflict("quote_closed", f"This quote is {quote.status}")
    warnings: list[str] = []
    if action == "decline":
        quote.status = "declined"
    else:
        quote.status = "interested"
        if message and message.strip():
            cleaned = scrub(message.strip(), names=(p.org.name,))
            quote.buyer_reply, warnings = cleaned.text, cleaned.warnings
    quote.updated_at = _now()
    request = await db.get(SourcingRequest, quote.request_id)
    notices: list[tuple[str, str, str]] = []
    if action != "decline":
        await set_system(db)
        try:
            invite = await db.get(SupplierInvite, quote.invite_id)
            if invite is not None and invite.status != "suppressed":
                token = invites.token_for(invite.id)
                notices.append((invite.email, f"A buyer wants to go ahead with your quote: {request.title}", (
                    f"Hello {invite.company_name} team,\n\nThe buyer reviewed your quote for \"{request.title}\" "
                    f"({quote.quantity_available:,} units at ${quote.unit_price:,.2f}) and wants to continue.\n\n"
                    "Register for free to read the buyer's reply, negotiate and close the deal on the platform "
                    f"({settings.invite_first_month_discount_pct}% off your first month of the Verified Supplier "
                    f"plan):\n{settings.app_base_url}/register?role=supplier&invite={token}\n\n"
                    f"Unsubscribe: {settings.app_base_url}/unsubscribe?token={token}\n\n"
                    f"{settings.platform_name}\n{settings.invite_sender_postal_address}\n")))
        finally:
            await set_tenant(db, p.org_id)
    await db.commit()
    return quote, warnings, notices


async def quotes_for(db: AsyncSession, request_id: uuid.UUID) -> list[GuestQuote]:
    return list((await db.scalars(select(GuestQuote).where(GuestQuote.request_id == request_id)
                                  .order_by(GuestQuote.created_at))).all())


# --- Registration ------------------------------------------------------------------------------------------------
async def convert_on_join(db: AsyncSession, invite: SupplierInvite, org_id: uuid.UUID, user_id: uuid.UUID) -> int:
    """Turn the new supplier's open guest quotes into masked negotiations (system context, inside registration)."""
    from app.marketplace import profiles, rfq

    quotes = (await db.scalars(select(GuestQuote).where(
        GuestQuote.invite_id == invite.id, GuestQuote.status.in_(("new", "interested"))))).all()
    if not quotes:
        return 0
    await profiles.refresh(db, org_id, restore_org=None)
    for quote in quotes:
        request = await db.get(SourcingRequest, quote.request_id)
        if request is None:
            continue
        total = (quote.unit_price * quote.quantity_available).quantize(Decimal("0.01"))
        thread = RfqThread(
            lot_id=None, lot_title=request.title[:200], buyer_org_id=quote.organization_id, supplier_org_id=org_id,
            created_by=user_id, status="open", offer_by="supplier", offer_price=total, shipping_terms=None,
            target_close_date=quote.available_by, custom_terms=quote.notes, request_id=request.id,
        )
        db.add(thread)
        await db.flush()
        offer = rfq._offer(total, None, quote.available_by, quote.notes)
        offer.update(unit_price=str(quote.unit_price), quantity=quote.quantity_available, condition=quote.condition)
        await rfq._append(db, thread, "supplier", user_id, "offer", offer=offer,
                          body=(f"Quote: {quote.quantity_available:,} units ({CONDITIONS[quote.condition]}) at "
                                f"${quote.unit_price:,.2f} each, available by {quote.available_by.isoformat()}."))
        if quote.buyer_reply:
            await rfq._append(db, thread, "buyer", None, "message", body=quote.buyer_reply)
        quote.status, quote.thread_id, quote.updated_at = "converted", thread.id, _now()
    await db.flush()
    return len(quotes)
