from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import onboarding as intake
from app.audit import record
from app.db import get_db
from app.deps import Principal, get_principal
from app.errors import forbidden
from app.rbac import Permission, has_permission
from app.services.auth import utcnow

# Deliberately built on get_principal, not require_permission: this is the one feature an account can reach
# before its onboarding is complete.
router = APIRouter(prefix="/onboarding", tags=["onboarding"])


class OnboardingOut(BaseModel):
    account_type: str
    completed: bool
    can_edit: bool
    fields: list[dict[str, Any]]
    profile: dict[str, Any]


class OnboardingIn(BaseModel):
    answers: dict[str, str | list[str]] = Field(max_length=20)


def _out(p: Principal) -> OnboardingOut:
    return OnboardingOut(
        account_type=p.org.account_type,
        completed=p.org.onboarding_completed,
        can_edit=has_permission(p.user.role, Permission.ORG_MANAGE),
        fields=intake.form_for(p.org.account_type),
        profile=p.org.onboarding_profile,
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
    profile = intake.validate_answers(p.org.account_type, body.answers)
    first_time = not p.org.onboarding_completed
    p.org.onboarding_profile = profile
    p.org.onboarding_completed = True
    p.org.onboarding_completed_at = p.org.onboarding_completed_at or utcnow()
    p.org.updated_at = utcnow()
    record(
        db, org_id=p.org_id, user_id=p.user.id, ip=p.ip,
        action="onboarding.completed" if first_time else "onboarding.updated",
        meta={"account_type": p.org.account_type},
    )
    await db.commit()
    return _out(p)
