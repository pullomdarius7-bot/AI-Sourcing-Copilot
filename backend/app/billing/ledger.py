"""Credit ledger: the only code that changes an account's credit balance.

One credit is one manifest analysis. Every change is a ledger row written in the same transaction as the
balance update, while the organization row is locked, so concurrent requests cannot overspend or double-grant.
Callers commit.
"""
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.errors import ApiError
from app.models import CreditLedger, Organization

settings = get_settings()

TRIAL_REFERENCE = "trial"


class InsufficientCredits(ApiError):
    def __init__(self) -> None:
        super().__init__(402, "insufficient_credits", "This account has no analysis credits left")


async def _lock(db: AsyncSession, org_id: uuid.UUID) -> Organization:
    org = await db.scalar(
        select(Organization).where(Organization.id == org_id).with_for_update().execution_options(populate_existing=True)
    )
    if org is None:
        raise ApiError(404, "not_found", "Organization not found")
    return org


async def post(
    db: AsyncSession, org_id: uuid.UUID, delta: int, kind: str, *,
    user_id: uuid.UUID | None = None, reference: str | None = None, note: str | None = None,
) -> CreditLedger:
    """Add (delta > 0) or spend (delta < 0) credits. A repeated reference returns the original entry unchanged."""
    if delta == 0:
        raise ValueError("delta must not be zero")
    org = await _lock(db, org_id)
    if reference is not None:
        existing = await db.scalar(
            select(CreditLedger).where(CreditLedger.organization_id == org_id, CreditLedger.reference == reference)
        )
        if existing is not None:
            return existing
    balance = org.credit_balance + delta
    if balance < 0:
        raise InsufficientCredits()
    org.credit_balance = balance
    entry = CreditLedger(
        organization_id=org_id, user_id=user_id, kind=kind, delta=delta, balance_after=balance,
        reference=reference, note=note,
    )
    db.add(entry)
    await db.flush()
    return entry


async def grant_trial(db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID | None) -> CreditLedger:
    """Start the free trial: a fixed number of analyses and a time limit, whichever runs out first. Once only."""
    entry = await post(
        db, org_id, settings.trial_credits, "trial_grant", user_id=user_id, reference=TRIAL_REFERENCE,
        note=f"{settings.trial_days}-day free trial",
    )
    org = await db.get(Organization, org_id)
    if org is not None and org.trial_ends_at is None:
        org.trial_ends_at = datetime.now(UTC) + timedelta(days=settings.trial_days)
    return entry
