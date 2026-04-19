"""Billing router — Stripe checkout + webhook handler."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.dependencies.auth import get_current_user
from app.models import Subscription, User
from app.schemas import CheckoutRequest, SubscriptionOut

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/billing", tags=["billing"])


@router.post("/checkout")
async def create_checkout(
    body: CheckoutRequest,
    current_user: User = Depends(get_current_user),
):
    from app.core.config import get_settings
    settings = get_settings()
    price_map = {
        "starter": settings.stripe_price_starter,
        "pro": settings.stripe_price_pro,
        "enterprise": settings.stripe_price_enterprise,
    }
    price_id = price_map.get(body.plan)
    if not price_id:
        raise HTTPException(status_code=400, detail=f"Unknown plan: {body.plan!r}")
    try:
        from billing_service.stripe_client import StripeClient
        client = StripeClient()
        result = await client.create_checkout_session(
            price_id=price_id,
            customer_email=current_user.email,
            success_url=body.success_url,
            cancel_url=body.cancel_url,
            metadata={"user_id": current_user.id, "plan": body.plan},
        )
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/subscription", response_model=SubscriptionOut)
async def get_subscription(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Subscription).where(Subscription.user_id == current_user.id)
    )
    sub = result.scalar_one_or_none()
    if not sub:
        raise HTTPException(status_code=404, detail="No active subscription")
    return sub


@router.post("/webhook")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    payload = await request.body()
    sig_header = request.headers.get("Stripe-Signature", "")
    try:
        from billing_service.stripe_client import StripeClient
        client = StripeClient()
        event = client.construct_webhook_event(payload, sig_header)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Webhook error: {exc}")

    event_type = event.get("type", "")
    if event_type == "checkout.session.completed":
        session = event["data"]["object"]
        meta = session.get("metadata", {})
        user_id = meta.get("user_id")
        plan = meta.get("plan", "starter")
        if user_id:
            existing = await db.execute(
                select(Subscription).where(Subscription.user_id == user_id)
            )
            sub = existing.scalar_one_or_none()
            if sub:
                sub.plan = plan
                sub.status = "active"
                sub.stripe_subscription_id = session.get("subscription")
                sub.stripe_customer_id = session.get("customer")
            else:
                sub = Subscription(
                    user_id=user_id,
                    plan=plan,
                    status="active",
                    stripe_subscription_id=session.get("subscription"),
                    stripe_customer_id=session.get("customer"),
                )
                db.add(sub)
    elif event_type == "customer.subscription.deleted":
        sub_obj = event["data"]["object"]
        result = await db.execute(
            select(Subscription).where(
                Subscription.stripe_subscription_id == sub_obj["id"]
            )
        )
        sub = result.scalar_one_or_none()
        if sub:
            sub.status = "canceled"
            sub.plan = "free"

    return {"received": True}
