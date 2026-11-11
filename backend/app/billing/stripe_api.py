"""Thin wrapper around the Stripe SDK. Everything Stripe-specific that touches the network lives here, so the
rest of the code (and the tests) deal with one small interface."""
import asyncio
from typing import Any

import stripe

from app.billing.plans import Plan, price_id
from app.config import get_settings
from app.errors import ApiError

settings = get_settings()

WEBHOOK_TOLERANCE_SECONDS = 300


class StripeNotConfigured(ApiError):
    def __init__(self) -> None:
        super().__init__(503, "billing_unavailable", "Billing is not configured yet")


class InvalidSignature(Exception):
    pass


def verify_webhook(payload: bytes, signature: str | None) -> None:
    """Raise InvalidSignature unless Stripe signed this exact payload recently with our webhook secret."""
    if not settings.stripe_webhook_secret:
        raise StripeNotConfigured()
    try:
        stripe.WebhookSignature.verify_header(
            payload.decode("utf-8"), signature or "", settings.stripe_webhook_secret, WEBHOOK_TOLERANCE_SECONDS
        )
    except (stripe.SignatureVerificationError, UnicodeDecodeError) as exc:
        raise InvalidSignature(str(exc)) from exc


class StripeAPI:
    def __init__(self) -> None:
        if not settings.stripe_secret_key:
            raise StripeNotConfigured()
        self._client = stripe.StripeClient(settings.stripe_secret_key)
        self._prices: dict[str, str] = {}

    async def _price(self, plan: Plan) -> str:
        configured = price_id(plan)
        if configured:
            return configured
        if plan.key not in self._prices:
            found = await asyncio.to_thread(
                self._client.v1.prices.list, params={"lookup_keys": [plan.key], "active": True, "limit": 1}
            )
            if not found.data:
                raise ApiError(503, "billing_unavailable", f"No Stripe price has the lookup key {plan.key}")
            self._prices[plan.key] = found.data[0].id
        return self._prices[plan.key]

    async def create_customer(self, *, email: str, name: str, metadata: dict[str, str]) -> str:
        customer = await asyncio.to_thread(
            self._client.v1.customers.create, params={"email": email, "name": name, "metadata": metadata}
        )
        return customer.id

    async def create_checkout_session(
        self, *, customer: str, plan: Plan, organization_id: str, metadata: dict[str, str],
        trial_days: int | None, success_url: str, cancel_url: str,
    ) -> str:
        subscription_data: dict[str, Any] = {"metadata": metadata}
        if trial_days:
            subscription_data["trial_period_days"] = trial_days
        session = await asyncio.to_thread(
            self._client.v1.checkout.sessions.create,
            params={
                "mode": "subscription",
                "customer": customer,
                "client_reference_id": organization_id,
                "line_items": [{"price": await self._price(plan), "quantity": 1}],
                "subscription_data": subscription_data,
                "metadata": metadata,
                "success_url": success_url,
                "cancel_url": cancel_url,
            },
        )
        return session.url

    async def create_topup_session(
        self, *, customer: str, credits: int, unit_amount_cents: int, organization_id: str, metadata: dict[str, str],
        success_url: str, cancel_url: str,
    ) -> str:
        """One-off payment for extra credits: the buyer pays exactly for the number of credits chosen."""
        session = await asyncio.to_thread(
            self._client.v1.checkout.sessions.create,
            params={
                "mode": "payment",
                "customer": customer,
                "client_reference_id": organization_id,
                "line_items": [{
                    "quantity": credits,
                    "price_data": {
                        "currency": "usd",
                        "unit_amount": unit_amount_cents,
                        "product_data": {"name": "Analysis credits",
                                         "description": "Pay-as-you-go credits; they roll over while you are subscribed."},
                    },
                }],
                "metadata": metadata,
                "payment_intent_data": {"metadata": metadata},
                "success_url": success_url,
                "cancel_url": cancel_url,
            },
        )
        return session.url

    async def create_portal_session(self, *, customer: str, return_url: str) -> str:
        session = await asyncio.to_thread(
            self._client.v1.billing_portal.sessions.create, params={"customer": customer, "return_url": return_url}
        )
        return session.url


_api: Any = None


def get_stripe() -> StripeAPI:
    global _api
    if _api is None:
        _api = StripeAPI()
    return _api


def set_stripe(api: Any) -> None:
    """Tests install a fake here."""
    global _api
    _api = api
