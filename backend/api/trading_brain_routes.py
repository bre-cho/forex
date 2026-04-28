from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter
from pydantic import BaseModel, Field

from engine.brain_bridge import TradingBrainBridge


class BrainPreviewRequest(BaseModel):
    symbol: str = "EURUSD"
    broker: str = "stub"
    signal: Dict[str, Any] = Field(default_factory=lambda: {
        "symbol": "EURUSD", "direction": "BUY", "confidence": 0.72, "rr": 1.8, "trend_strength": 0.65
    })
    context: Dict[str, Any] = Field(default_factory=lambda: {
        "broker_connected": True, "market_data_ok": True, "spread_pips": 1.2, "atr_pips": 8.0,
        "account_equity": 10000.0, "open_positions": 0, "daily_loss_pct": 0.0, "consecutive_losses": 0,
        "session_score": 0.7, "volatility_score": 0.6, "risk_pct": 0.5
    })


def build_trading_brain_router(app_state: Any) -> APIRouter:
    router = APIRouter(prefix="/api/trading-brain", tags=["trading-brain"])
    bridge = TradingBrainBridge(app_state)

    @router.get("/health")
    def trading_brain_health() -> Dict[str, Any]:
        return bridge.health()

    @router.post("/preview-cycle")
    def preview_cycle(payload: BrainPreviewRequest) -> Dict[str, Any]:
        return bridge.preview_cycle(
            symbol=payload.symbol,
            broker=payload.broker,
            signal=payload.signal,
            context=payload.context,
        )

    return router
