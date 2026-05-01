"""Billing router — Stripe checkout + webhook handler."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.dependencies.auth import get_current_user
from app.models import BotInstance, Subscription, User
from app.schemas import CheckoutRequest, SubscriptionOut

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/billing", tags=["billing"])

# Number of failed payment attempts before hard revocation of subscription.
_MAX_PAYMENT_FAILURE_ATTEMPTS = 3


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


async def _pause_live_bots_for_user(user_id: str, db: AsyncSession) -> int:
    """Stop all live bots for a user after subscription cancellation/lapse.

    Returns the number of bots transitioned to 'paused' status.
    """
    try:
        from app.core.registry import get_registry
        registry = get_registry()

        # Find all workspace IDs for this user
        from app.models import WorkspaceMember
        member_result = await db.execute(
            select(WorkspaceMember).where(WorkspaceMember.user_id == user_id)
        )
        workspace_ids = [m.workspace_id for m in member_result.scalars().all()]
        if not workspace_ids:
            return 0

        # Find live bots in those workspaces
        bots_result = await db.execute(
            select(BotInstance).where(
                BotInstance.workspace_id.in_(workspace_ids),
                BotInstance.mode == "live",
                BotInstance.status == "running",
            )
        )
        live_bots = bots_result.scalars().all()
        paused = 0
        for bot in live_bots:
            if registry is not None:
                runtime = registry.get(bot.id)
                if runtime is not None:
                    try:
                        await runtime.pause()
                    except Exception as exc:
                        logger.warning(
                            "Failed to pause bot %s after subscription change: %s", bot.id, exc
                        )
            bot.status = "paused"
            paused += 1
        return paused
    except Exception as exc:
        logger.error("_pause_live_bots_for_user failed for user %s: %s", user_id, exc)
        return 0


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
            logger.info("Stripe checkout completed for user %s → plan=%s", user_id, plan)

    elif event_type == "customer.subscription.updated":
        # Handle plan upgrades/downgrades and status changes.
        sub_obj = event["data"]["object"]
        result = await db.execute(
            select(Subscription).where(
                Subscription.stripe_subscription_id == sub_obj["id"]
            )
        )
        sub = result.scalar_one_or_none()
        if sub:
            new_status = str(sub_obj.get("status", "active"))
            new_plan_items = sub_obj.get("items", {}).get("data", [])
            if new_plan_items:
                # Extract plan nickname from first item's price metadata
                price = new_plan_items[0].get("price", {})
                nickname = price.get("nickname") or price.get("metadata", {}).get("plan")
                if nickname:
                    sub.plan = str(nickname).lower()
            sub.status = new_status
            logger.info(
                "Stripe subscription updated: sub_id=%s status=%s plan=%s",
                sub_obj["id"], new_status, sub.plan,
            )

    elif event_type == "customer.subscription.deleted":
        # P5.2: Revoke entitlements + pause live bots.
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
            paused = await _pause_live_bots_for_user(sub.user_id, db)
            logger.info(
                "Stripe subscription deleted: user=%s → downgraded to free, %d live bots paused",
                sub.user_id, paused,
            )

    elif event_type == "invoice.payment_failed":
        # P5.2: Payment failure — mark subscription as past_due and log warning.
        # Live bots are NOT immediately paused to allow a grace period.
        # After repeated failures (handled by invoice.payment_action_required),
        # the subscription.deleted event will trigger the actual revocation.
        invoice = event["data"]["object"]
        stripe_customer_id = invoice.get("customer")
        attempt_count = int(invoice.get("attempt_count", 1))
        if stripe_customer_id:
            result = await db.execute(
                select(Subscription).where(
                    Subscription.stripe_customer_id == stripe_customer_id
                )
            )
            sub = result.scalar_one_or_none()
            if sub:
                sub.status = "past_due"
                logger.warning(
                    "Stripe invoice payment failed: customer=%s attempt=%d "
                    "subscription=%s status→past_due",
                    stripe_customer_id, attempt_count, sub.stripe_subscription_id,
                )
                # Hard revocation after max failed attempts (grace period exhausted).
                if attempt_count >= _MAX_PAYMENT_FAILURE_ATTEMPTS:
                    sub.status = "canceled"
                    sub.plan = "free"
                    paused = await _pause_live_bots_for_user(sub.user_id, db)
                    logger.error(
                        "Stripe payment failed %d times: user=%s → revoked, %d bots paused",
                        attempt_count, sub.user_id, paused,
                    )

    await db.commit()
    return {"received": True}
