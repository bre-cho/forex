"""Auth router — register, login, token refresh, logout."""
from __future__ import annotations

import logging
import time
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import get_redis
from app.core.db import get_db
from app.core.security import (
    create_access_token,
    create_password_reset_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.core.token_revocation import normalize_iat_ms, revoke_all_user_access_tokens
from app.dependencies.auth import get_current_user
from app.models import User
from app.schemas import (
    ForgotPasswordRequest,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenResponse,
    UserOut,
)

router = APIRouter(prefix="/v1/auth", tags=["auth"])
logger = logging.getLogger(__name__)

# Password-reset tokens are valid for 1 hour
_RESET_TOKEN_TTL_MINUTES = 60
_REVOKED_REFRESH_TOKEN_PREFIX = "auth:revoked:refresh:"
_USER_REFRESH_REVOKED_AFTER_PREFIX = "auth:revoke_after:user:"


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")
    user = User(
        email=body.email,
        hashed_password=hash_password(body.password),
        full_name=body.full_name,
    )
    db.add(user)
    await db.flush()
    return user


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")
    return TokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    if await _is_refresh_token_revoked(body.refresh_token):
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    try:
        payload = decode_token(body.refresh_token)
        if payload.get("type") != "refresh":
            raise ValueError("Not a refresh token")
        user_id = payload["sub"]
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found")
    if await _is_user_refresh_revoked_after(
        user_id,
        normalize_iat_ms(payload.get("iat_ms", payload.get("iat"))),
    ):
        raise HTTPException(status_code=401, detail="Refresh token revoked")
    return TokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )


@router.post("/logout")
async def logout(body: RefreshRequest):
    """Revoke the provided refresh token and terminate the client session."""
    user_id = _extract_refresh_user_id(body.refresh_token)
    if user_id:
        await _revoke_all_user_refresh_tokens(user_id)
        await revoke_all_user_access_tokens(user_id)
    else:
        await _revoke_refresh_token(body.refresh_token)
    return {"message": "Logged out"}


@router.post("/forgot-password")
async def forgot_password(body: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)):
    """Generate a password-reset token and send it via email.

    Always returns 200 regardless of whether the email is registered,
    to prevent email enumeration attacks.
    """
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if user and user.is_active:
        reset_token = create_password_reset_token(
            user.id,
            expires_delta=timedelta(minutes=_RESET_TOKEN_TTL_MINUTES),
        )
        await _send_reset_email(user.email, reset_token)

    return {"message": "If that email is registered, a reset link has been sent."}


async def _send_reset_email(email: str, reset_token: str) -> None:
    """Send a password-reset email.  Falls back to logging when SMTP is not configured."""
    from app.core.config import get_settings

    settings = get_settings()
    reset_url = f"{settings.frontend_url}/reset-password?token={reset_token}"

    if not settings.smtp_username or not settings.smtp_password:
        # SMTP not configured — do not log reset token content.
        logger.warning(
            "SMTP not configured. Password-reset requested for %s",
            email,
        )
        return

    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    subject = f"[{settings.app_name}] Password Reset"
    body_html = (
        f"<p>You requested a password reset.</p>"
        f"<p><a href='{reset_url}'>Click here to reset your password</a></p>"
        f"<p>This link expires in {_RESET_TOKEN_TTL_MINUTES} minutes.</p>"
        f"<p>If you did not request this, please ignore this email.</p>"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_email}>"
    msg["To"] = email
    msg.attach(MIMEText(body_html, "html"))

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            if settings.smtp_tls:
                smtp.starttls()
            smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.sendmail(settings.smtp_from_email, email, msg.as_string())
        logger.info("Password-reset email sent to %s", email)
    except Exception as exc:
        # Do not leak SMTP errors to the caller
        logger.error("Failed to send password-reset email to %s: %s", email, exc)


@router.post("/reset-password")
async def reset_password(body: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
    try:
        payload = decode_token(body.token)
        if payload.get("purpose") != "password_reset":
            raise ValueError("Invalid token purpose")
        user_id = payload["sub"]
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.hashed_password = hash_password(body.new_password)
    await _revoke_all_user_refresh_tokens(user.id)
    await revoke_all_user_access_tokens(user.id)
    return {"message": "Password updated"}


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)):
    return current_user


async def _revoke_refresh_token(refresh_token: str) -> None:
    try:
        payload = decode_token(refresh_token)
        if payload.get("type") != "refresh":
            return
        exp = int(payload.get("exp", 0))
    except Exception:
        return
    ttl = max(exp - int(time.time()), 1)
    redis = await get_redis()
    await redis.setex(f"{_REVOKED_REFRESH_TOKEN_PREFIX}{refresh_token}", ttl, "1")


async def _is_refresh_token_revoked(refresh_token: str) -> bool:
    redis = await get_redis()
    return bool(await redis.exists(f"{_REVOKED_REFRESH_TOKEN_PREFIX}{refresh_token}"))


async def _revoke_all_user_refresh_tokens(user_id: str) -> None:
    redis = await get_redis()
    await redis.set(f"{_USER_REFRESH_REVOKED_AFTER_PREFIX}{user_id}", int(time.time() * 1000))


async def _is_user_refresh_revoked_after(user_id: str, token_iat: int | None) -> bool:
    if token_iat is None:
        return False
    redis = await get_redis()
    value = await redis.get(f"{_USER_REFRESH_REVOKED_AFTER_PREFIX}{user_id}")
    if value is None:
        return False
    try:
        return int(token_iat) <= int(value)
    except (TypeError, ValueError):
        return False


def _extract_refresh_user_id(refresh_token: str) -> str | None:
    try:
        payload = decode_token(refresh_token)
        if payload.get("type") != "refresh":
            return None
        user_id = payload.get("sub")
        if isinstance(user_id, str) and user_id:
            return user_id
    except Exception:
        return None
    return None
