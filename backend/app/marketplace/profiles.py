"""Masked marketplace identities.

Buyers and sellers never see each other's company name, people or contact details while they negotiate. Each
account appears under a stable alias (derived with a server secret, so it cannot be reversed) and a few earned
badges. The public projection lives in market_profiles, which every account can read; it is rebuilt from the
private account data by the system, never edited by hand.
"""
import base64
import hashlib
import hmac
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import set_system, set_tenant
from app.marketplace.catalog import REGIONS
from app.models import MarketplaceLot, MarketProfile, Organization, RfqThread

settings = get_settings()


def alias_for(org_id: uuid.UUID, side: str) -> str:
    digest = hmac.new(settings.jwt_secret.encode(), f"market-alias:{org_id}".encode(), hashlib.sha256).digest()
    code = base64.b32encode(digest).decode()[:6]
    return f"{'S' if side == 'seller' else 'B'}-{code}"


def _seller_label(org: Organization) -> str:
    types = set(org.onboarding_profile.get("inventory_types") or [])
    if types & {"customer_returns", "salvage"}:
        return "Liquidator"
    if types & {"overstock", "shelf_pulls"}:
        return "Wholesaler"
    return "Supplier"


def deals_badge(n: int) -> str:
    if n >= 50:
        return "50+ Lots Completed"
    if n >= 10:
        return "10+ Lots Completed"
    if n >= 1:
        return f"{n} Lot{'s' if n != 1 else ''} Completed"
    return "New on the marketplace"


def badges(profile: MarketProfile | None) -> list[str]:
    if profile is None:
        return []
    label = profile.label
    if profile.verified:
        label = f"Verified {label}"
    if profile.hub_region:
        label = f"{label} - {REGIONS.get(profile.hub_region, profile.hub_region)} Hub"
    return [label, deals_badge(profile.completed_deals)]


def public(profile: MarketProfile | None) -> dict:
    if profile is None:
        return {"alias": "Unknown", "label": "", "verified": False, "badges": [], "completed_deals": 0, "member_since": None}
    return {
        "alias": profile.alias, "label": profile.label, "verified": profile.verified, "badges": badges(profile),
        "completed_deals": profile.completed_deals, "member_since": profile.member_since,
    }


async def refresh(db: AsyncSession, org_id: uuid.UUID, *, restore_org: uuid.UUID | None) -> MarketProfile:
    """Rebuild one account's public profile from its private data. Runs with system rights (it counts deals
    across both parties' threads) and returns to `restore_org`'s tenant context afterwards (None: stay system)."""
    await set_system(db)
    try:
        org = await db.get(Organization, org_id)
        if org is None:
            raise LookupError(org_id)
        side = "seller" if org.account_type == "supplier" else "buyer"
        deals = await db.scalar(
            select(func.count()).select_from(RfqThread).where(
                RfqThread.status == "accepted",
                (RfqThread.supplier_org_id == org_id) if side == "seller" else (RfqThread.buyer_org_id == org_id),
            )
        ) or 0
        hub = None
        if side == "seller":
            hub = await db.scalar(
                select(MarketplaceLot.region).where(
                    MarketplaceLot.organization_id == org_id, MarketplaceLot.region.is_not(None),
                    MarketplaceLot.status.in_(("published", "sold")),
                ).group_by(MarketplaceLot.region).order_by(func.count().desc()).limit(1)
            )
            hub = hub or next(iter(org.onboarding_profile.get("dispatch_regions") or []), None)
        label = _seller_label(org) if side == "seller" else ("Reseller" if org.account_type == "solo_buyer" else "Buying Team")
        profile = await db.get(MarketProfile, org_id)
        if profile is None:
            profile = MarketProfile(organization_id=org_id, alias=alias_for(org_id, side), member_since=org.created_at)
            db.add(profile)
        profile.side, profile.label, profile.hub_region = side, label, hub
        profile.alias = alias_for(org_id, side)
        profile.verified = org.billing_exempt or org.subscription_status in ("active", "trialing")
        profile.completed_deals = int(deals)
        profile.updated_at = datetime.now(UTC)
        await db.flush()
        return profile
    finally:
        if restore_org is not None:
            await set_tenant(db, restore_org)
