"""Seat limits: how many people an account may have. A seat is an active user or an open invitation."""
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.plans import PLANS
from app.config import get_settings
from app.errors import ApiError
from app.models import Invitation, Organization, User

settings = get_settings()


def seat_limit(org: Organization) -> int | None:
    """The plan's seats while subscribed; before that 1 for a solo account and trial_seats for a team.
    None means no limit."""
    plan = PLANS.get(org.plan or "") if org.subscription_status in ("trialing", "active", "past_due") else None
    if plan is not None:
        return plan.seats
    if org.account_type == "solo_buyer":
        return 1
    if org.account_type == "org_buyer":
        return settings.trial_seats
    return None


async def seats_used(db: AsyncSession, org_id: uuid.UUID) -> int:
    users = await db.scalar(select(func.count()).select_from(User).where(
        User.organization_id == org_id, User.is_active.is_(True)
    )) or 0
    invites = await db.scalar(select(func.count()).select_from(Invitation).where(
        Invitation.organization_id == org_id, Invitation.accepted_at.is_(None), Invitation.revoked_at.is_(None),
        Invitation.expires_at > func.now(),
    )) or 0
    return int(users) + int(invites)


async def ensure_free_seat(db: AsyncSession, org: Organization, *, already_counted: bool = False) -> None:
    """Raise 409 seat_limit_reached unless one more person fits. already_counted: the person holds an open
    invitation, which already takes a seat."""
    limit = seat_limit(org)
    if limit is None:
        return
    used = await seats_used(db, org.id)
    if used + (0 if already_counted else 1) > limit:
        raise ApiError(
            409, "seat_limit_reached",
            f"Your plan includes {limit} seat{'s' if limit != 1 else ''} and all are taken "
            "(active people plus open invitations). Deactivate someone, revoke an invitation or upgrade the plan.",
            extra={"seats_used": used, "seat_limit": limit},
        )
