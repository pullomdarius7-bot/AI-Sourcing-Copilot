"""Whether an account may spend credits on AI right now."""
from datetime import UTC, datetime

from app.errors import ApiError
from app.models import Organization

UPGRADE_URL = "/billing"

_REASONS = {
    "trial_expired": "Your free trial has ended. Choose a plan to keep analysing.",
    "past_due": "Your last payment failed. Update your payment method to resume analysing.",
    "cancelled": "Your subscription has ended. Choose a plan to keep analysing.",
    "no_subscription": "Choose a plan to start analysing.",
}


class PaymentRequired(ApiError):
    """HTTP 402: {"error": {"code": "INSUFFICIENT_CREDITS" | "SUBSCRIPTION_REQUIRED", "remaining", "upgrade_url"}}."""

    def __init__(self, code: str, message: str, remaining: int, reason: str) -> None:
        super().__init__(
            402, code, message, extra={"remaining": remaining, "upgrade_url": UPGRADE_URL, "reason": reason}
        )
        self.reason = reason


def access_problem(org: Organization, now: datetime | None = None) -> str | None:
    """None when the account has an active subscription or an unexpired trial; otherwise why it does not."""
    status = org.subscription_status
    if status in ("active", "trialing"):
        return None
    if status in ("past_due", "cancelled"):
        return status
    if org.trial_ends_at is None:
        return "no_subscription"
    return None if (now or datetime.now(UTC)) < org.trial_ends_at else "trial_expired"


def ensure_can_spend(org: Organization, cost: int) -> None:
    reason = access_problem(org)
    if reason is not None:
        raise PaymentRequired("SUBSCRIPTION_REQUIRED", _REASONS[reason], org.credit_balance, reason)
    if org.credit_balance < cost:
        raise PaymentRequired(
            "INSUFFICIENT_CREDITS",
            f"This needs {cost} credit{'s' if cost != 1 else ''} and the account has {org.credit_balance} left.",
            org.credit_balance, "insufficient_credits",
        )
