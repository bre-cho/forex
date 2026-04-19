"""Stripe client wrapper."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class StripeClient:
    def __init__(self, secret_key: Optional[str] = None) -> None:
        self._key = secret_key or os.getenv("STRIPE_SECRET_KEY", "")
        if not self._key:
            logger.warning("StripeClient: STRIPE_SECRET_KEY not set")

    def _get_stripe(self):
        import stripe
        stripe.api_key = self._key
        return stripe

    async def create_checkout_session(
        self,
        price_id: str,
        customer_email: str,
        success_url: str,
        cancel_url: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        stripe = self._get_stripe()
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="subscription",
            customer_email=customer_email,
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata=metadata or {},
        )
        return {"session_id": session.id, "url": session.url}

    async def create_customer_portal(self, customer_id: str, return_url: str) -> str:
        stripe = self._get_stripe()
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url,
        )
        return session.url

    async def get_subscription(self, subscription_id: str) -> Dict[str, Any]:
        stripe = self._get_stripe()
        sub = stripe.Subscription.retrieve(subscription_id)
        return dict(sub)

    async def cancel_subscription(self, subscription_id: str) -> Dict[str, Any]:
        stripe = self._get_stripe()
        sub = stripe.Subscription.delete(subscription_id)
        return dict(sub)

    def construct_webhook_event(self, payload: bytes, sig_header: str) -> Any:
        stripe = self._get_stripe()
        webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
        return stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
