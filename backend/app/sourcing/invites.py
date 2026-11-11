"""Invitations to businesses the sourcing engine finds on the web.

When a buyer's request finds a supplier that is not on the platform, the supplier is told that buyers want what it
sells and is invited to join and quote. Safeguards, all enforced here:

- Only an address the business published on its own website is used (that publication is the basis for contacting
  it about its business), never a guessed or bought address.
- The buyer stays anonymous, and only requests the buyer opened to suppliers are mentioned.
- One email per business domain per INVITE_COOLDOWN_DAYS, however many buyers ask; later requests are added to the
  same invitation. A global daily cap protects the platform's sending reputation.
- Every email identifies the platform, says why the recipient got it, carries a one-click unsubscribe link and the
  sender's postal address (anti-spam law). Unsubscribing suppresses the address and the domain for good. Without a
  postal address configured, invitations are recorded but held.
- Businesses already on the platform are not invited.
- SUPPLIER_INVITES=review records invitations without sending; =off disables them.
"""
import hashlib
import hmac
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import email as mail
from app.config import get_settings
from app.db import set_system, set_tenant
from app.jobs import queue
from app.marketplace.catalog import CATEGORIES, REGIONS
from app.models import EmailSuppression, Organization, SourcingCandidate, SourcingRequest, SupplierInvite, User
from app.sourcing.extract import FREE_MAIL

settings = get_settings()
SEND = "sourcing.invite"


def _now() -> datetime:
    return datetime.now(UTC)


def token_for(invite_id: uuid.UUID) -> str:
    return hmac.new(settings.jwt_secret.encode(), f"supplier-invite:{invite_id}".encode(), hashlib.sha256).hexdigest()[:40]


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def _suppressed(db: AsyncSession, email: str, domain: str) -> bool:
    return await db.scalar(select(EmailSuppression.value).where(
        EmailSuppression.value.in_([email.lower(), domain.lower(), email.split("@")[1].lower()])
    ).limit(1)) is not None


async def _already_member(db: AsyncSession, domain: str) -> bool:
    if domain in FREE_MAIL:
        return False
    return await db.scalar(select(User.id).join(Organization, Organization.id == User.organization_id).where(
        or_(User.email.ilike(f"%@{domain}"), User.email.ilike(f"%.{domain}")), Organization.account_type == "supplier",
    ).limit(1)) is not None


def _demand(request: SourcingRequest) -> dict[str, Any]:
    return {
        "request_id": str(request.id), "title": request.title, "category": request.category,
        "quantity": request.quantity, "region": request.region,
        "needed_by": request.needed_by.isoformat() if request.needed_by else None, "at": _now().isoformat(),
    }


async def consider(db: AsyncSession, request: SourcingRequest, profile, candidate: SourcingCandidate) -> str | None:
    """Record (and, when every safeguard passes, queue) an invitation for a web-found supplier. Returns the status
    shown to the buyer: queued, invited (recently), held, member, suppressed, joined, no_contact, or None when invites
    are off or the request is private."""
    if settings.supplier_invites == "off" or not request.open_to_suppliers:
        return None
    if not profile.contact_email or not profile.domain:
        return "no_contact"
    await set_system(db)
    try:
        if await _suppressed(db, profile.contact_email, profile.domain):
            return "suppressed"
        if await _already_member(db, profile.domain):
            return "member"
        invite = await db.scalar(select(SupplierInvite).where(SupplierInvite.domain == profile.domain).with_for_update())
        if invite is None:
            invite = SupplierInvite(id=uuid.uuid4(), domain=profile.domain, email=profile.contact_email,
                                    company_name=profile.company_name, source_url=profile.contact_url or profile.website or "",
                                    status="pending", demand=[], request_count=0, sent_count=0)
            invite.token_hash = _hash(token_for(invite.id))
            db.add(invite)
            await db.flush()
        candidate.invite_id = invite.id
        if invite.status in ("suppressed", "joined"):
            return invite.status
        if all(d.get("request_id") != str(request.id) for d in invite.demand):
            invite.demand = ([_demand(request)] + list(invite.demand))[:20]
            invite.request_count += 1
        invite.updated_at = _now()
        await db.flush()
        cooled = invite.last_sent_at is None or invite.last_sent_at < _now() - timedelta(days=settings.invite_cooldown_days)
        if settings.supplier_invites != "auto":
            invite.status, invite.status_note = "held", "Invitations are in review mode"
            return "held"
        if not cooled:
            return "invited"  # emailed within the cooldown; this request was added to that invitation
        await queue.enqueue(db, org_id=request.organization_id, kind=SEND, payload={"invite_id": str(invite.id)},
                            dedupe_key=f"{SEND}:{invite.id}", max_attempts=3)
        return "queued"
    finally:
        await db.flush()  # write the invitation with system rights before returning to the buyer's scope
        await set_tenant(db, request.organization_id)


def invite_email(invite: SupplierInvite, token: str) -> tuple[str, str]:
    wanted = invite.demand[:3]
    first = wanted[0] if wanted else {}
    topic = CATEGORIES.get(first.get("category") or "", None) or first.get("title") or "products you sell"
    lines = []
    for d in wanted:
        detail = [f"{d['quantity']:,} units" if d.get("quantity") else None,
                  CATEGORIES.get(d.get("category") or "", None),
                  f"delivery to the {REGIONS[d['region']]}" if d.get("region") in REGIONS else None,
                  f"needed by {d['needed_by']}" if d.get("needed_by") else None]
        lines.append(f"  - {d['title']}" + (f" ({', '.join(x for x in detail if x)})" if any(detail) else ""))
    more = invite.request_count - len(wanted)
    base = settings.app_base_url
    body = (
        f"Hello {invite.company_name} team,\n\n"
        f"Buyers on {settings.platform_name}, a B2B marketplace for wholesale and liquidation inventory, are looking "
        f"for products your website lists:\n\n" + "\n".join(lines) +
        (f"\n  ...and {more} more request(s)." if more > 0 else "") +
        "\n\nSend a quote now, free and without an account (it takes two minutes):\n"
        f"{base}/quote?token={token}\n\n"
        "If a buyer wants to go ahead, you register for free to continue the conversation and close the deal on the "
        f"platform. Suppliers who join through this invitation get {settings.invite_first_month_discount_pct}% off "
        f"their first month of the Verified Supplier plan:\n{base}/register?role=supplier&invite={token}\n\n"
        f"Why you received this: we found {invite.email} published on {invite.domain} while searching for suppliers "
        "of these products. We will not email you about this again for "
        f"{settings.invite_cooldown_days} days.\n\n"
        f"Unsubscribe (one click, permanent): {base}/unsubscribe?token={token}\n\n"
        f"{settings.platform_name}\n{settings.invite_sender_postal_address}\n"
    )
    return f"Buyers are looking for {topic} - send a free quote on {settings.platform_name}", body


async def handle_send(db: AsyncSession, job) -> dict[str, Any]:
    await set_system(db)
    invite = await db.get(SupplierInvite, uuid.UUID(job.payload["invite_id"]), with_for_update=True)
    if invite is None or invite.status in ("suppressed", "joined"):
        return {"skipped": invite.status if invite else "missing"}
    if settings.supplier_invites != "auto":
        invite.status, invite.status_note = "held", "Invitations are in review mode"
    elif not settings.invite_sender_postal_address.strip():
        invite.status, invite.status_note = "held", "Set INVITE_SENDER_POSTAL_ADDRESS to send invitations"
    elif await _suppressed(db, invite.email, invite.domain):
        invite.status = "suppressed"
    elif invite.last_sent_at and invite.last_sent_at > _now() - timedelta(days=settings.invite_cooldown_days):
        return {"skipped": "cooldown"}
    else:
        sent_today = await db.scalar(select(func.count()).select_from(SupplierInvite).where(
            SupplierInvite.last_sent_at > _now() - timedelta(days=1))) or 0
        if sent_today >= settings.invite_daily_limit:
            invite.status, invite.status_note = "held", "Daily invitation limit reached"
        else:
            subject, body = invite_email(invite, token_for(invite.id))
            await mail.send_email(invite.email, subject, body)
            invite.status, invite.status_note = "sent", None
            invite.sent_count += 1
            invite.last_sent_at = _now()
    invite.updated_at = _now()
    await db.commit()
    return {"status": invite.status}


async def unsubscribe(db: AsyncSession, token: str) -> SupplierInvite | None:
    await set_system(db)
    invite = await db.scalar(select(SupplierInvite).where(SupplierInvite.token_hash == _hash(token)).with_for_update())
    if invite is None:
        await db.rollback()
        return None
    for value in {invite.email.lower(), invite.domain.lower()}:
        if await db.get(EmailSuppression, value) is None:
            db.add(EmailSuppression(value=value, reason="unsubscribed"))
    invite.status, invite.status_note, invite.updated_at = "suppressed", "Unsubscribed", _now()
    await db.commit()
    return invite


async def mark_joined(db: AsyncSession, token: str, org_id: uuid.UUID) -> SupplierInvite | None:
    """Called at registration (system context) when the sign-up came from an invitation link."""
    invite = await db.scalar(select(SupplierInvite).where(SupplierInvite.token_hash == _hash(token)).with_for_update())
    if invite is None or invite.status in ("suppressed", "joined"):
        return None
    invite.status, invite.joined_org_id, invite.updated_at = "joined", org_id, _now()
    return invite


async def invite_preview(db: AsyncSession, token: str) -> dict[str, Any] | None:
    """What the registration page shows a supplier arriving from an invitation (no buyer identities)."""
    await set_system(db)
    invite = await db.scalar(select(SupplierInvite).where(SupplierInvite.token_hash == _hash(token)))
    preview = None
    if invite is not None and invite.status != "suppressed":
        preview = {"company_name": invite.company_name, "request_count": invite.request_count,
                   "demand": [{k: d.get(k) for k in ("title", "category", "quantity", "region", "needed_by")}
                              for d in invite.demand[:5]]}
    await db.rollback()
    return preview
