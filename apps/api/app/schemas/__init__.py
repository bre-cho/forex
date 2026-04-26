"""Pydantic v2 schemas for all API domains."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator


# ── Auth ──────────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: str = Field(min_length=1, max_length=255)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8)


# ── Users ─────────────────────────────────────────────────────────────────────

class UserOut(BaseModel):
    id: str
    email: str
    full_name: str
    is_active: bool
    is_superuser: bool
    email_verified: bool
    avatar_url: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    avatar_url: Optional[str] = None


# ── Workspaces ────────────────────────────────────────────────────────────────

class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9\-]+$")


class WorkspaceUpdate(BaseModel):
    name: Optional[str] = None
    settings: Optional[Dict[str, Any]] = None


class WorkspaceOut(BaseModel):
    id: str
    name: str
    slug: str
    owner_id: str
    plan: str
    created_at: datetime

    model_config = {"from_attributes": True}


class WorkspaceMemberOut(BaseModel):
    id: str
    workspace_id: str
    user_id: str
    role: str
    joined_at: datetime

    model_config = {"from_attributes": True}


class AddMemberRequest(BaseModel):
    email: EmailStr
    role: str = "viewer"


# ── Broker Connections ────────────────────────────────────────────────────────

class BrokerConnectionCreate(BaseModel):
    name: str
    broker_type: Literal["paper", "ctrader", "mt5", "bybit"]
    credentials: Dict[str, Any] = Field(default_factory=dict)


class BrokerConnectionUpdate(BaseModel):
    name: Optional[str] = None
    credentials: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None


class BrokerConnectionOut(BaseModel):
    id: str
    workspace_id: str
    name: str
    broker_type: str
    is_active: bool
    last_synced_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Strategies ────────────────────────────────────────────────────────────────

class StrategyCreate(BaseModel):
    name: str
    description: str = ""
    is_public: bool = False
    config: Dict[str, Any] = Field(default_factory=dict)


class StrategyUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_public: Optional[bool] = None
    config: Optional[Dict[str, Any]] = None


class StrategyOut(BaseModel):
    id: str
    workspace_id: str
    name: str
    description: str
    is_public: bool
    config: Dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}


class PublicStrategyOut(BaseModel):
    id: str
    name: str
    description: str
    created_at: datetime

    model_config = {"from_attributes": True}


class StrategyVersionOut(BaseModel):
    id: str
    strategy_id: str
    version: int
    config_snapshot: Dict[str, Any]
    change_notes: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Bot Instances ─────────────────────────────────────────────────────────────

class BotCreate(BaseModel):
    name: str
    symbol: str = "EURUSD"
    timeframe: str = "M5"
    mode: Literal["paper", "demo", "live"] = "paper"
    strategy_id: Optional[str] = None
    broker_connection_id: Optional[str] = None


class BotUpdate(BaseModel):
    name: Optional[str] = None
    symbol: Optional[str] = None
    timeframe: Optional[str] = None
    mode: Optional[Literal["paper", "demo", "live"]] = None
    strategy_id: Optional[str] = None
    broker_connection_id: Optional[str] = None


class BotConfigUpdate(BaseModel):
    risk_json: Optional[Dict[str, Any]] = None
    strategy_config: Optional[Dict[str, Any]] = None
    ai_json: Optional[Dict[str, Any]] = None


class BotOut(BaseModel):
    id: str
    workspace_id: str
    name: str
    symbol: str
    timeframe: str
    mode: str
    status: str
    strategy_id: Optional[str] = None
    broker_connection_id: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class BotRuntimeSnapshotOut(BaseModel):
    id: str
    bot_instance_id: str
    snapshot: Dict[str, Any]
    recorded_at: datetime

    model_config = {"from_attributes": True}


# ── Signals ───────────────────────────────────────────────────────────────────

class SignalOut(BaseModel):
    id: str
    bot_instance_id: str
    symbol: str
    direction: str
    confidence: float
    wave_state: str
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Orders / Trades ───────────────────────────────────────────────────────────

class OrderOut(BaseModel):
    id: str
    bot_instance_id: str
    broker_order_id: str
    symbol: str
    side: str
    order_type: str
    volume: float
    price: Optional[float] = None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class TradeOut(BaseModel):
    id: str
    bot_instance_id: str
    broker_trade_id: str
    symbol: str
    side: str
    volume: float
    entry_price: float
    exit_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    pnl: Optional[float] = None
    commission: float
    status: str
    closed_volume: float
    remaining_volume: float
    opened_at: datetime
    closed_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ── Notifications ─────────────────────────────────────────────────────────────

class NotificationOut(BaseModel):
    id: str
    user_id: str
    title: str
    body: str
    type: str
    is_read: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Billing ───────────────────────────────────────────────────────────────────

class CheckoutRequest(BaseModel):
    plan: str
    success_url: str
    cancel_url: str


class SubscriptionOut(BaseModel):
    id: str
    user_id: str
    plan: str
    status: str
    current_period_end: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ── Pagination ────────────────────────────────────────────────────────────────

class PaginatedResponse(BaseModel):
    items: List[Any]
    total: int
    page: int
    page_size: int
    has_next: bool
