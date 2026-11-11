"""The one door to every model call.

Nothing reaches an AI provider without a Permit, and a Permit only exists after the account has been checked
and charged:

    permit = await gateway.acquire(db, org_id, action, subject_id, units=...)   # 402 if not allowed
    response = await gateway.dispatch(permit, provider, request)                 # as many calls as the action needs
    await gateway.settle(db, permit)      # with the result, in the result's transaction
    await gateway.release(db, permit)     # on failure: the credits come back

acquire() locks the account row, verifies subscription-or-trial and balance, deducts the credits and commits,
so two simultaneous uploads can never both spend the last credit. If a worker dies mid-call the charge stays
'held'; the retry of the same subject reuses it instead of charging twice.
"""
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers import LLMProvider, LLMRequest, LLMResponse
from app.billing import ledger
from app.billing.access import PaymentRequired, ensure_can_spend
from app.billing.plans import cost_of
from app.config import get_settings
from app.db import set_tenant
from app.models import CreditTransaction, Organization

settings = get_settings()

__all__ = ["PaymentRequired", "Permit", "acquire", "check", "dispatch", "release", "settle"]


@dataclass
class Permit:
    org_id: uuid.UUID
    action: str
    subject_id: uuid.UUID
    cost: int
    transaction_id: int | None = None  # the held charge; None when billing is not enforced
    closed: bool = False


async def check(db: AsyncSession, org_id: uuid.UUID, action: str, *, units: int = 0) -> int:
    """Pre-flight for an endpoint that queues AI work: raises 402 now instead of failing later. Charges nothing."""
    cost = cost_of(action, units)
    if settings.billing_enforced:
        org = await db.get(Organization, org_id)
        if org is not None:
            await db.refresh(org)
            ensure_can_spend(org, cost)
    return cost


async def _held(db: AsyncSession, org_id: uuid.UUID, action: str, subject_id: uuid.UUID) -> CreditTransaction | None:
    return await db.scalar(
        select(CreditTransaction).where(
            CreditTransaction.organization_id == org_id, CreditTransaction.action == action,
            CreditTransaction.subject_id == subject_id, CreditTransaction.kind == "usage",
            CreditTransaction.state == "held",
        )
    )


async def acquire(
    db: AsyncSession, org_id: uuid.UUID, action: str, subject_id: uuid.UUID, *,
    units: int = 0, user_id: uuid.UUID | None = None,
) -> Permit:
    """Verify the account, deduct the credits and commit. Raises PaymentRequired (402) and charges nothing if
    the account has no active subscription or trial, or not enough credits."""
    cost = cost_of(action, units)
    if not settings.billing_enforced:
        return Permit(org_id, action, subject_id, 0)
    org = await ledger.lock(db, org_id)
    if cost == 0:  # included in something already paid for: the account still needs access
        try:
            ensure_can_spend(org, 0)
        finally:
            await db.commit()
            await set_tenant(db, org_id)
        return Permit(org_id, action, subject_id, 0)
    held = await _held(db, org_id, action, subject_id)
    if held is None:
        try:
            ensure_can_spend(org, cost)
        except PaymentRequired:
            await db.commit()  # release the row lock; nothing was charged
            await set_tenant(db, org_id)
            raise
        held = await ledger.post(
            db, org_id, -cost, "usage", user_id=user_id, action=action, subject_id=subject_id, state="held",
            note=action.replace("_", " "),
        )
    permit = Permit(org_id, action, subject_id, -held.delta, held.id)
    await db.commit()
    await set_tenant(db, org_id)
    return permit


async def dispatch(permit: Permit, provider: LLMProvider, request: LLMRequest) -> LLMResponse:
    """Call a model. This is the only place in the code base that does."""
    if permit.closed:
        raise RuntimeError("This permit has already been settled or released")
    return await provider.generate(request)


async def settle(db: AsyncSession, permit: Permit) -> None:
    """The work succeeded: the charge is final. Call inside the transaction that stores the result."""
    permit.closed = True
    if permit.transaction_id is None:
        return
    entry = await db.get(CreditTransaction, permit.transaction_id)
    if entry is not None and entry.state == "held":
        entry.state = "settled"


async def release(db: AsyncSession, permit: Permit, *, commit: bool = True) -> None:
    """The work failed: give the credits back. Safe to call twice."""
    permit.closed = True
    if permit.transaction_id is None:
        return
    if commit:
        await db.rollback()
        await set_tenant(db, permit.org_id)
    await ledger.lock(db, permit.org_id)
    entry = await db.get(CreditTransaction, permit.transaction_id, populate_existing=True)
    if entry is not None and entry.state == "held":
        entry.state = "refunded"
        await ledger.post(
            db, permit.org_id, -entry.delta, "refund", user_id=entry.user_id, action=permit.action,
            subject_id=permit.subject_id, note=f"{permit.action.replace('_', ' ')} did not complete",
        )
    if commit:
        await db.commit()
        await set_tenant(db, permit.org_id)
