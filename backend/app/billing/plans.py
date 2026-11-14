"""Subscription plans and what each metered AI action costs. One credit is the unit everything is priced in."""
import math
from dataclasses import dataclass

from app.config import get_settings
from app.onboarding import AccountType

settings = get_settings()


@dataclass(frozen=True)
class Plan:
    key: str  # also the Stripe price lookup key
    name: str
    price_cents: int
    monthly_credits: int
    account_types: tuple[AccountType, ...]  # who may buy it
    seats: int | None  # people (active users + open invitations); None = no limit
    topup_price_cents: int  # pay-as-you-go price of one extra credit


PLANS: dict[str, Plan] = {
    p.key: p
    for p in (
        Plan("solo_buyer_monthly", "Solo Operator", 7900, settings.plan_credits_solo,
             (AccountType.SOLO_BUYER, AccountType.ORG_BUYER), seats=1,
             topup_price_cents=settings.topup_price_cents_solo),
        Plan("enterprise_buyer_monthly", "Sourcing Team", 24900, settings.plan_credits_enterprise,
             (AccountType.SOLO_BUYER, AccountType.ORG_BUYER), seats=5,
             topup_price_cents=settings.topup_price_cents_enterprise),
        Plan("supplier_monthly", "Verified Supplier", 14900, settings.plan_credits_supplier,
             (AccountType.SUPPLIER,), seats=None, topup_price_cents=settings.topup_price_cents_supplier),
        # Supplier seats are unlimited on purpose: more people answering buyers means faster replies, and a seat
        # costs nothing to run.
    )
}

TEAM_PLAN = "enterprise_buyer_monthly"


def team_features(org) -> bool:
    """Sourcing Team features (margin rules, freight overlays, export API, priority processing): on the team plan,
    and for team accounts during their free trial so they can evaluate them."""
    if getattr(org, "billing_exempt", False):
        return True
    if org.plan == TEAM_PLAN and org.subscription_status in ("trialing", "active", "past_due"):
        return True
    return org.account_type == "org_buyer" and org.subscription_status == "none"


def plans_for(account_type: str) -> list[Plan]:
    return [p for p in PLANS.values() if AccountType(account_type) in p.account_types]


def price_id(plan: Plan) -> str | None:
    """Stripe price id from the environment, if one is configured; otherwise the price is found by lookup key."""
    return {
        "solo_buyer_monthly": settings.stripe_price_solo_buyer_monthly,
        "enterprise_buyer_monthly": settings.stripe_price_enterprise_buyer_monthly,
        "supplier_monthly": settings.stripe_price_supplier_monthly,
    }[plan.key] or None


def plan_for_price(price: str | None, lookup_key: str | None = None) -> Plan | None:
    if lookup_key in PLANS:
        return PLANS[lookup_key]
    if price:
        for plan in PLANS.values():
            if price_id(plan) == price:
                return plan
    return None


# --- Metered actions ---------------------------------------------------------------------------------
MANIFEST_EXTRACTION = "manifest_extraction"  # parsing and normalizing an uploaded manifest or document
INCLUDED_ANALYSIS = "included_analysis"  # first analysis of a buyer's own manifest: part of its upload credit
WEB_SOURCING = "web_sourcing"  # one web supplier search for a sourcing request
DEEP_ANALYSIS = "deep_analysis"  # one full analysis run over a document
ASSISTANT_ANSWER = "assistant_answer"  # one model-written assistant answer

MANIFEST_FLAT_LIMIT = 500  # files under this many line items cost one credit
MANIFEST_ITEMS_PER_CREDIT = 100


def cost_of(action: str, units: int = 0) -> int:
    """Credits an action costs. `units` is the number of line items for manifest extraction, unused otherwise."""
    if action == MANIFEST_EXTRACTION:
        if units < MANIFEST_FLAT_LIMIT:
            return 1
        return math.ceil(units / MANIFEST_ITEMS_PER_CREDIT)
    if action == INCLUDED_ANALYSIS:
        return 0
    if action == WEB_SOURCING:
        return settings.credit_cost_web_sourcing
    if action == DEEP_ANALYSIS:
        return settings.credit_cost_deep_analysis
    if action == ASSISTANT_ANSWER:
        return settings.credit_cost_assistant_answer
    raise ValueError(f"unknown metered action: {action}")
