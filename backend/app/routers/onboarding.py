from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import onboarding as intake
from app.audit import record
from app.billing import ledger
from app.db import get_db
from app.deps import Principal, get_principal
from app.errors import conflict, forbidden
from app.onboarding import AccountType
from app.rbac import Permission, has_permission
from app.services.auth import utcnow

# Deliberately built on get_principal, not require_permission: this is the one feature an account can reach
# before its onboarding is complete.
router = APIRouter(prefix="/onboarding", tags=["onboarding"])


class OnboardingOut(BaseModel):
    account_type: AccountType
    organization_name: str
    completed: bool
    can_edit: bool
    roles: list[dict[str, str]]
    forms: dict[str, list[dict[str, Any]]]
    profile: dict[str, Any]
    credit_balance: int
    trial_ends_at: datetime | None


class OnboardingIn(BaseModel):
    # Step 1 of the wizard. Can be chosen until onboarding is complete, then the account type is fixed.
    account_type: AccountType | None = None
    organization_name: str | None = Field(default=None, min_length=2, max_length=200)
    answers: dict[str, str | list[str]] = Field(max_length=20)


def _out(p: Principal) -> OnboardingOut:
    return OnboardingOut(
        account_type=p.org.account_type,
        organization_name=p.org.name,
        completed=p.org.onboarding_completed,
        can_edit=has_permission(p.user.role, Permission.ORG_MANAGE),
        roles=intake.ROLES,
        forms=intake.all_forms(),
        profile=p.org.onboarding_profile,
        credit_balance=p.org.credit_balance,
        trial_ends_at=p.org.trial_ends_at,
    )


@router.get("", response_model=OnboardingOut)
async def get_onboarding(p: Principal = Depends(get_principal)):
    return _out(p)


@router.put("", response_model=OnboardingOut)
async def submit_onboarding(
    body: OnboardingIn, p: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)
):
    if not has_permission(p.user.role, Permission.ORG_MANAGE):
        raise forbidden("Only an administrator of this account can complete its profile")
    # Lock the account so two simultaneous submissions cannot both count as the first one.
    await db.refresh(p.org, with_for_update=True)
    first_time = not p.org.onboarding_completed
    account_type = (body.account_type or AccountType(p.org.account_type)).value
    if account_type != p.org.account_type and not first_time:
        raise conflict("account_type_locked", "The account type cannot be changed after onboarding")
    profile = intake.validate_answers(account_type, body.answers)

    p.org.account_type = account_type
    p.org.onboarding_profile = profile
    p.org.onboarding_completed = True
    p.org.onboarding_completed_at = p.org.onboarding_completed_at or utcnow()
    if body.organization_name and body.organization_name.strip():
        p.org.name = body.organization_name.strip()
    p.org.updated_at = utcnow()
    if first_time:
        await ledger.grant_trial(db, p.org_id, p.user.id)
    record(
        db, org_id=p.org_id, user_id=p.user.id, ip=p.ip,
        action="onboarding.completed" if first_time else "onboarding.updated",
        meta={"account_type": account_type},
    )
    await db.commit()
    return _out(p)
