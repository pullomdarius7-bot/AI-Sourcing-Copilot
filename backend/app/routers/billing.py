import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import plans as catalog
from app.billing import seats, service
from app.billing.access import access_problem
from app.billing.stripe_api import InvalidSignature, verify_webhook
from app.config import get_settings
from app.db import get_db, set_system
from app.deps import Principal, require_permission
from app.errors import ApiError
from app.models import CreditTransaction
from app.rbac import Permission, has_permission

settings = get_settings()
router = APIRouter(prefix="/billing", tags=["billing"])
webhooks = APIRouter(prefix="/webhooks", tags=["webhooks"])


class PlanOut(BaseModel):
    key: str
    name: str
    price_cents: int
    monthly_credits: int
    seats: int | None
    topup_price_cents: int


class PublicPlanOut(PlanOut):
    account_types: list[str]


class TopUpOut(BaseModel):
    available: bool
    reason: str | None
    price_cents: int | None
    min_credits: int
    max_credits: int
    available_at_or_below: int


class SeatsOut(BaseModel):
    used: int
    limit: int | None


class TopUpIn(BaseModel):
    credits: int


class TransactionOut(BaseModel):
    id: int
    kind: str
    action: str | None
    subject_id: str | None
    delta: int
    balance_after: int
    state: str
    note: str | None
    created_at: datetime


class BillingOut(BaseModel):
    plan: str | None
    subscription_status: str
    credit_balance: int
    trial_ends_at: datetime | None
    current_period_end: datetime | None
    ai_access: bool
    ai_access_reason: str | None
    can_manage: bool
    has_billing_profile: bool
    checkout_trial_days: int | None
    purchased_credits: int
    topup: TopUpOut
    seats: SeatsOut
    plans: list[PlanOut]
    costs: dict[str, Any]
    transactions: list[TransactionOut]


class CheckoutIn(BaseModel):
    plan: str


class UrlOut(BaseModel):
    url: str


def _plan_out(plan: catalog.Plan) -> PlanOut:
    return PlanOut(key=plan.key, name=plan.name, price_cents=plan.price_cents, monthly_credits=plan.monthly_credits,
                   seats=plan.seats, topup_price_cents=plan.topup_price_cents)


def _topup_out(org) -> TopUpOut:
    available, reason, plan = service.topup_status(org)
    return TopUpOut(available=available, reason=reason, price_cents=plan.topup_price_cents if plan else None,
                    min_credits=settings.topup_min_credits, max_credits=settings.topup_max_credits,
                    available_at_or_below=settings.topup_available_at_or_below)


@router.get("/plans", response_model=list[PublicPlanOut])
async def public_plans():
    """The price list, for the public pricing page. The one source of prices, credits and seats."""
    return [PublicPlanOut(**_plan_out(p).model_dump(), account_types=[t.value for t in p.account_types])
            for p in catalog.PLANS.values()]


@router.get("", response_model=BillingOut)
async def summary(p: Principal = Depends(require_permission(Permission.ORG_READ)), db: AsyncSession = Depends(get_db)):
    rows = await db.scalars(
        select(CreditTransaction).where(CreditTransaction.organization_id == p.org_id)
        .order_by(CreditTransaction.id.desc()).limit(50)
    )
    reason = access_problem(p.org)
    return BillingOut(
        plan=p.org.plan, subscription_status=p.org.subscription_status, credit_balance=p.org.credit_balance,
        trial_ends_at=p.org.trial_ends_at, current_period_end=p.org.current_period_end,
        ai_access=reason is None, ai_access_reason=reason,
        can_manage=has_permission(p.user.role, Permission.BILLING_MANAGE),
        has_billing_profile=p.org.stripe_customer_id is not None,
        checkout_trial_days=service.checkout_trial_days(p.org),
        purchased_credits=p.org.purchased_credits,
        topup=_topup_out(p.org),
        seats=SeatsOut(used=await seats.seats_used(db, p.org_id), limit=seats.seat_limit(p.org)),
        plans=[_plan_out(x) for x in catalog.plans_for(p.org.account_type)],
        costs={
            catalog.MANIFEST_EXTRACTION: {
                "credits": 1, "flat_under_items": catalog.MANIFEST_FLAT_LIMIT,
                "items_per_credit": catalog.MANIFEST_ITEMS_PER_CREDIT,
            },
            catalog.DEEP_ANALYSIS: {"credits": catalog.cost_of(catalog.DEEP_ANALYSIS)},
            catalog.ASSISTANT_ANSWER: {"credits": catalog.cost_of(catalog.ASSISTANT_ANSWER)},
        },
        transactions=[
            TransactionOut(id=r.id, kind=r.kind, action=r.action, subject_id=str(r.subject_id) if r.subject_id else None,
                           delta=r.delta, balance_after=r.balance_after, state=r.state, note=r.note,
                           created_at=r.created_at)
            for r in rows
        ],
    )


@router.post("/create-checkout-session", response_model=UrlOut)
async def create_checkout_session(
    body: CheckoutIn, p: Principal = Depends(require_permission(Permission.BILLING_MANAGE)),
    db: AsyncSession = Depends(get_db),
):
    return UrlOut(url=await service.create_checkout(db, p, body.plan))


@router.post("/create-topup-session", response_model=UrlOut)
async def create_topup_session(
    body: TopUpIn, p: Principal = Depends(require_permission(Permission.BILLING_MANAGE)),
    db: AsyncSession = Depends(get_db),
):
    return UrlOut(url=await service.create_topup(db, p, body.credits))


@router.post("/create-portal-session", response_model=UrlOut)
async def create_portal_session(
    p: Principal = Depends(require_permission(Permission.BILLING_MANAGE)), db: AsyncSession = Depends(get_db)
):
    return UrlOut(url=await service.create_portal(db, p))


@webhooks.post("/stripe")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Authenticated by Stripe's signature over the raw body, never by a session."""
    payload = await request.body()
    try:
        verify_webhook(payload, request.headers.get("stripe-signature"))
    except InvalidSignature:
        raise ApiError(400, "invalid_signature", "The webhook signature is not valid") from None
    try:
        event = json.loads(payload)
    except ValueError:
        raise ApiError(400, "invalid_payload", "The webhook body is not JSON") from None
    await set_system(db)
    return {"received": True, "result": await service.handle_event(db, event)}
