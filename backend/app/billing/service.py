"""Subscriptions: starting a checkout, opening the customer portal, and applying Stripe webhook events."""
import logging
import math
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import email as mail
from app.audit import record
from app.billing import ledger
from app.billing.plans import PLANS, TEAM_PLAN, Plan, plan_for_price
from app.billing.stripe_api import get_stripe
from app.config import get_settings
from app.deps import Principal
from app.errors import bad_request, conflict
from app.marketplace import profiles
from app.models import MarketProfile, Organization, User
from app.onboarding import AccountType

logger = logging.getLogger("app.billing")
settings = get_settings()

LIVE_STATUSES = ("trialing", "active", "past_due")
TOPUP = "credit_topup"
_STRIPE_STATUS = {
    "trialing": "trialing", "active": "active",
    "past_due": "past_due", "unpaid": "past_due", "incomplete": "past_due", "paused": "past_due",
    "canceled": "cancelled", "incomplete_expired": "cancelled",
}


def _now() -> datetime:
    return datetime.now(UTC)


def _ts(value: Any) -> datetime | None:
    return datetime.fromtimestamp(value, UTC) if isinstance(value, int | float) else None


# --- Checkout and portal -----------------------------------------------------------------------------
def checkout_trial_days(org: Organization) -> int | None:
    """Days of trial to carry into the subscription: whatever is left of the account's free trial, and only
    while it still has trial credits to use. An account that has run out pays now and gets its quota now."""
    if org.stripe_subscription_id or org.subscription_status != "none":
        return None
    if org.trial_ends_at is None or org.credit_balance <= 0:
        return None
    remaining = (org.trial_ends_at - _now()).total_seconds() / 86400
    return min(settings.trial_days, math.ceil(remaining)) if remaining > 0 else None


async def ensure_customer(db: AsyncSession, p: Principal) -> str:
    org = await ledger.lock(db, p.org_id)  # one customer per account, even under simultaneous clicks
    if org.stripe_customer_id is None:
        org.stripe_customer_id = await get_stripe().create_customer(
            email=p.user.email, name=org.name,
            metadata={"organization_id": str(org.id), "user_id": str(p.user.id)},
        )
        record(db, org_id=org.id, user_id=p.user.id, action="billing.customer_created", ip=p.ip)
    customer = org.stripe_customer_id
    await db.commit()
    return customer


async def create_checkout(db: AsyncSession, p: Principal, plan_key: str) -> str:
    plan = PLANS.get(plan_key)
    if plan is None:
        raise bad_request("unknown_plan", "That plan does not exist")
    if AccountType(p.org.account_type) not in plan.account_types:
        raise bad_request("plan_not_available", f"The {plan.name} plan is not available for this type of account")
    if p.org.subscription_status in LIVE_STATUSES:
        raise conflict("already_subscribed", "This account already has a subscription. Manage it in the billing portal.")
    customer = await ensure_customer(db, p)
    metadata = {"organization_id": str(p.org_id), "user_id": str(p.user.id), "plan": plan.key}
    return await get_stripe().create_checkout_session(
        customer=customer, plan=plan, organization_id=str(p.org_id), metadata=metadata,
        trial_days=checkout_trial_days(p.org),
        success_url=f"{settings.app_base_url}/billing?checkout=success",
        cancel_url=f"{settings.app_base_url}/billing?checkout=cancelled",
    )


# --- Pay-as-you-go top-ups ---------------------------------------------------------------------------
def topup_status(org: Organization) -> tuple[bool, str | None, Plan | None]:
    """Whether the account may buy extra credits now, and why not. Top-ups are for subscribers who have run out
    (or nearly out) of their monthly credits; they are not a way around the free trial."""
    plan = PLANS.get(org.plan or "")
    if org.subscription_status not in ("active", "trialing") or plan is None:
        return False, "Extra credits are available to subscribers. Choose a plan first.", plan
    if org.credit_balance > settings.topup_available_at_or_below:
        return False, (f"Extra credits become available when you are down to {settings.topup_available_at_or_below} "
                       "credits or fewer."), plan
    return True, None, plan


async def create_topup(db: AsyncSession, p: Principal, credits: int) -> str:
    available, reason, plan = topup_status(p.org)
    if not available or plan is None:
        raise conflict("topup_unavailable", reason or "Extra credits are not available")
    if not settings.topup_min_credits <= credits <= settings.topup_max_credits:
        raise bad_request("invalid_quantity",
                          f"Buy between {settings.topup_min_credits} and {settings.topup_max_credits} credits")
    customer = await ensure_customer(db, p)
    metadata = {
        "organization_id": str(p.org_id), "user_id": str(p.user.id), "kind": TOPUP,
        "credits": str(credits), "unit_amount": str(plan.topup_price_cents),
    }
    return await get_stripe().create_topup_session(
        customer=customer, credits=credits, unit_amount_cents=plan.topup_price_cents, organization_id=str(p.org_id),
        metadata=metadata, success_url=f"{settings.app_base_url}/billing?topup=success",
        cancel_url=f"{settings.app_base_url}/billing?topup=cancelled",
    )


async def _topup_paid(db: AsyncSession, obj: dict[str, Any]) -> Organization | None:
    """Grant purchased credits once Stripe confirms the money arrived. The amount is checked against what was
    priced at checkout, so a tampered or partial session grants nothing."""
    if obj.get("payment_status") != "paid":
        return None  # delayed payment methods: checkout.session.async_payment_succeeded follows
    org = await _find_org(db, obj)
    if org is None:
        return None
    try:
        credits = int(_get(obj, "metadata", "credits"))
        unit = int(_get(obj, "metadata", "unit_amount"))
    except (TypeError, ValueError):
        logger.error("top-up session %s has no credit metadata", obj.get("id"))
        return org
    if credits <= 0 or obj.get("amount_total") != credits * unit or (obj.get("currency") or "usd") != "usd":
        logger.error("top-up session %s: amount %s does not match %s credits at %s", obj.get("id"),
                     obj.get("amount_total"), credits, unit)
        return org
    entry = await ledger.post(db, org.id, credits, "purchase", reference=f"topup:{obj.get('id')}", purchased=True,
                              note=f"{credits} credits bought")
    record(db, org_id=org.id, user_id=None, action="billing.credits_purchased",
           meta={"credits": credits, "amount": obj.get("amount_total"), "session": obj.get("id"),
                 "balance": entry.balance_after})
    return org


async def create_portal(db: AsyncSession, p: Principal) -> str:
    if p.org.stripe_customer_id is None:
        raise conflict("no_subscription", "This account has no billing profile yet. Choose a plan first.")
    return await get_stripe().create_portal_session(
        customer=p.org.stripe_customer_id, return_url=f"{settings.app_base_url}/billing"
    )


# --- Webhook events ----------------------------------------------------------------------------------
def _get(obj: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _id(value: Any) -> str | None:
    """Stripe sends related objects either as an id or expanded."""
    if isinstance(value, dict):
        value = value.get("id")
    return value if isinstance(value, str) else None


async def _find_org(db: AsyncSession, obj: dict[str, Any]) -> Organization | None:
    for candidate in (obj.get("client_reference_id"), _get(obj, "metadata", "organization_id"),
                      _get(obj, "subscription_details", "metadata", "organization_id"),
                      _get(obj, "parent", "subscription_details", "metadata", "organization_id")):
        try:
            org = await db.get(Organization, uuid.UUID(str(candidate))) if candidate else None
        except ValueError:
            org = None
        if org is not None:
            return org
    customer = _id(obj.get("customer"))
    if customer:
        return await db.scalar(select(Organization).where(Organization.stripe_customer_id == customer))
    return None


def _plan_of_lines(lines: Any) -> Plan | None:
    for line in lines if isinstance(lines, list) else []:
        price = line.get("price") if isinstance(line.get("price"), dict) else {}
        plan = (
            plan_for_price(price.get("id"), price.get("lookup_key"))
            or plan_for_price(_id(_get(line, "pricing", "price_details", "price")))
            or PLANS.get(_get(line, "metadata", "plan") or "")
        )
        if plan:
            return plan
    return None


def _apply_plan(org: Organization, plan: Plan | None) -> None:
    if plan is None:
        return
    org.plan = plan.key
    if plan.key == TEAM_PLAN and org.account_type == AccountType.SOLO_BUYER:
        org.account_type = AccountType.ORG_BUYER.value  # buying the team plan turns a solo account into a team


async def _checkout_completed(db: AsyncSession, obj: dict[str, Any]) -> Organization | None:
    if obj.get("mode") == "payment" and _get(obj, "metadata", "kind") == TOPUP:
        return await _topup_paid(db, obj)
    org = await _find_org(db, obj)
    if org is None or obj.get("mode") != "subscription":
        return None
    org = await ledger.lock(db, org.id)
    org.stripe_customer_id = _id(obj.get("customer")) or org.stripe_customer_id
    org.stripe_subscription_id = _id(obj.get("subscription")) or org.stripe_subscription_id
    plan = PLANS.get(_get(obj, "metadata", "plan") or "")
    _apply_plan(org, plan)
    paid = obj.get("payment_status") == "paid"
    if paid:
        org.subscription_status = "active"
        invoice = _id(obj.get("invoice"))
        if plan and invoice:
            await ledger.set_balance(db, org.id, plan.monthly_credits, reference=f"invoice:{invoice}",
                                     note=f"{plan.name} monthly credits")
    elif org.subscription_status in ("none", "cancelled"):
        org.subscription_status = "trialing"  # card on file, first payment when the trial ends
    record(db, org_id=org.id, user_id=None, action="billing.checkout_completed",
           meta={"plan": org.plan, "status": org.subscription_status})
    return org


async def _invoice_paid(db: AsyncSession, obj: dict[str, Any]) -> Organization | None:
    org = await _find_org(db, obj)
    if org is None:
        return None
    org = await ledger.lock(db, org.id)
    subscription = _id(obj.get("subscription")) or _id(_get(obj, "parent", "subscription_details", "subscription"))
    org.stripe_subscription_id = subscription or org.stripe_subscription_id
    lines = _get(obj, "lines", "data")
    plan = _plan_of_lines(lines) or PLANS.get(org.plan or "")
    _apply_plan(org, plan)
    if (obj.get("amount_paid") or 0) <= 0:
        if org.subscription_status == "none":
            org.subscription_status = "trialing"  # the zero invoice that opens a trial grants nothing
        return org
    org.subscription_status = "active"
    period_end = max((_get(line, "period", "end") or 0 for line in lines or []), default=0)
    org.current_period_end = _ts(period_end) if period_end else org.current_period_end
    if plan is None:
        logger.error("paid invoice %s for organization %s matches no plan; no credits granted", obj.get("id"), org.id)
        return org
    granted = await ledger.set_balance(db, org.id, plan.monthly_credits, reference=f"invoice:{obj.get('id')}",
                                       note=f"{plan.name} monthly credits")
    if granted:
        record(db, org_id=org.id, user_id=None, action="billing.credits_refilled",
               meta={"plan": plan.key, "credits": plan.monthly_credits, "invoice": obj.get("id")})
    return org


async def _invoice_failed(db: AsyncSession, obj: dict[str, Any]) -> Organization | None:
    org = await _find_org(db, obj)
    if org is None:
        return None
    org = await ledger.lock(db, org.id)
    org.subscription_status = "past_due"  # AI access is paused until a payment succeeds
    record(db, org_id=org.id, user_id=None, action="billing.payment_failed", meta={"invoice": obj.get("id")})
    admins = await db.scalars(
        select(User).where(User.organization_id == org.id, User.role == "admin", User.is_active.is_(True))
    )
    subject, body = mail.payment_failed_email(org.name)
    for admin in admins:
        await mail.send_email(admin.email, subject, body)
    return org


async def _subscription_updated(db: AsyncSession, obj: dict[str, Any]) -> Organization | None:
    org = await _find_org(db, obj)
    if org is None:
        return None
    org = await ledger.lock(db, org.id)
    if org.stripe_subscription_id not in (None, obj.get("id")):
        return org  # an older subscription of the same customer
    org.stripe_subscription_id = obj.get("id")
    _apply_plan(org, _plan_of_lines(_get(obj, "items", "data")) or PLANS.get(_get(obj, "metadata", "plan") or ""))
    status = _STRIPE_STATUS.get(obj.get("status") or "")
    if status == "cancelled":
        return await _subscription_deleted(db, obj)
    if status:
        org.subscription_status = status
    org.current_period_end = _ts(obj.get("current_period_end")) or org.current_period_end
    return org


async def _subscription_deleted(db: AsyncSession, obj: dict[str, Any]) -> Organization | None:
    org = await _find_org(db, obj)
    if org is None:
        return None
    org = await ledger.lock(db, org.id)
    if org.stripe_subscription_id not in (None, obj.get("id")):
        return org
    org.subscription_status, org.plan, org.stripe_subscription_id = "cancelled", None, None
    org.current_period_end = None
    await ledger.wipe(db, org.id, reference=f"subscription:{obj.get('id')}:cancelled",
                      note="Subscription cancelled")
    record(db, org_id=org.id, user_id=None, action="billing.subscription_cancelled")
    return org


HANDLERS = {
    "checkout.session.completed": _checkout_completed,
    "checkout.session.async_payment_succeeded": _checkout_completed,
    "invoice.payment_succeeded": _invoice_paid,
    "invoice.payment_failed": _invoice_failed,
    "customer.subscription.updated": _subscription_updated,
    "customer.subscription.deleted": _subscription_deleted,
}


async def handle_event(db: AsyncSession, event: dict[str, Any]) -> str:
    """Apply one verified Stripe event exactly once. Returns processed | duplicate | ignored."""
    event_id, event_type = str(event.get("id") or ""), str(event.get("type") or "")
    obj = _get(event, "data", "object")
    handler = HANDLERS.get(event_type)
    if not event_id or handler is None or not isinstance(obj, dict):
        return "ignored"
    first = await db.scalar(
        text("INSERT INTO stripe_events (id, type) VALUES (:id, :type) ON CONFLICT (id) DO NOTHING RETURNING id"),
        {"id": event_id[:255], "type": event_type[:80]},
    )
    if first is None:
        await db.rollback()
        return "duplicate"
    org = await handler(db, obj)
    if org is None:
        logger.warning("stripe event %s (%s) matches no organization", event_id, event_type)
    else:
        org.updated_at = _now()
        if await db.get(MarketProfile, org.id) is not None:
            await profiles.refresh(db, org.id, restore_org=None)  # the "Verified" badge follows the subscription
    await db.commit()  # the event marker, the account change and the credits commit together or not at all
    return "processed"
