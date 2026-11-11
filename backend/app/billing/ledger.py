"""Credit ledger: the only code that changes an account's credit balance.

Every change is a credit_transactions row written in the same transaction as the balance update, while the
organization row is locked (SELECT ... FOR UPDATE), so concurrent requests cannot overspend or double-grant.
Callers commit.
"""
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.access import PaymentRequired
from app.config import get_settings
from app.errors import ApiError
from app.models import CreditTransaction, Organization

settings = get_settings()

TRIAL_REFERENCE = "trial"


class InsufficientCredits(PaymentRequired):
    def __init__(self, remaining: int = 0) -> None:
        super().__init__(
            "INSUFFICIENT_CREDITS", "This account has no analysis credits left", remaining, "insufficient_credits"
        )


async def lock(db: AsyncSession, org_id: uuid.UUID) -> Organization:
    """Lock the account row. Every balance change and every spend decision happens under this lock."""
    org = await db.scalar(
        select(Organization).where(Organization.id == org_id).with_for_update().execution_options(populate_existing=True)
    )
    if org is None:
        raise ApiError(404, "not_found", "Organization not found")
    return org


async def post(
    db: AsyncSession, org_id: uuid.UUID, delta: int, kind: str, *,
    user_id: uuid.UUID | None = None, reference: str | None = None, note: str | None = None,
    action: str | None = None, subject_id: uuid.UUID | None = None, state: str = "settled",
) -> CreditTransaction:
    """Add (delta > 0) or spend (delta < 0) credits. A repeated reference returns the original entry unchanged."""
    if delta == 0:
        raise ValueError("delta must not be zero")
    org = await lock(db, org_id)
    if reference is not None:
        existing = await db.scalar(
            select(CreditTransaction).where(
                CreditTransaction.organization_id == org_id, CreditTransaction.reference == reference
            )
        )
        if existing is not None:
            return existing
    balance = org.credit_balance + delta
    if balance < 0:
        raise InsufficientCredits(org.credit_balance)
    org.credit_balance = balance
    entry = CreditTransaction(
        organization_id=org_id, user_id=user_id, kind=kind, delta=delta, balance_after=balance,
        reference=reference, note=note, action=action, subject_id=subject_id, state=state,
    )
    db.add(entry)
    await db.flush()
    return entry


async def set_balance(db: AsyncSession, org_id: uuid.UUID, amount: int, *, reference: str, note: str) -> bool:
    """Start a billing period: unused credits expire and the plan's quota is granted. Once per reference."""
    org = await lock(db, org_id)
    seen = await db.scalar(
        select(CreditTransaction.id).where(
            CreditTransaction.organization_id == org_id, CreditTransaction.reference == reference
        )
    )
    if seen is not None:
        return False
    if org.credit_balance > 0:
        await post(db, org_id, -org.credit_balance, "expiry", reference=f"{reference}:expiry",
                   note="Unused credits do not roll over")
    await post(db, org_id, amount, "plan_grant", reference=reference, note=note)
    return True


async def wipe(db: AsyncSession, org_id: uuid.UUID, *, reference: str, note: str) -> None:
    org = await lock(db, org_id)
    if org.credit_balance > 0:
        await post(db, org_id, -org.credit_balance, "expiry", reference=reference, note=note)


async def grant_trial(db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID | None) -> CreditTransaction:
    """Start the free trial: a fixed number of analyses and a time limit, whichever runs out first. Once only."""
    entry = await post(
        db, org_id, settings.trial_credits, "trial_grant", user_id=user_id, reference=TRIAL_REFERENCE,
        note=f"{settings.trial_days}-day free trial",
    )
    org = await db.get(Organization, org_id)
    if org is not None and org.trial_ends_at is None:
        org.trial_ends_at = datetime.now(UTC) + timedelta(days=settings.trial_days)
    return entry
