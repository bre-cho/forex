"""Signal builder — constructs structured signals from engine output."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional


@dataclass
class TradingSignal:
    signal_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    bot_instance_id: str = ""
    symbol: str = ""
    direction: str = ""        # 'buy' | 'sell' | 'close'
    confidence: float = 0.0
    wave_state: str = ""
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    timeframe: str = "M5"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "bot_instance_id": self.bot_instance_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "confidence": self.confidence,
            "wave_state": self.wave_state,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "timeframe": self.timeframe,
            "created_at": self.created_at.isoformat(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TradingSignal":
        created_at = data.get("created_at")
        if isinstance(created_at, str):
            try:
                from datetime import datetime, timezone
                created_at = datetime.fromisoformat(created_at)
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
            except (ValueError, AttributeError):
                from datetime import datetime, timezone
                created_at = datetime.now(timezone.utc)
        elif not isinstance(created_at, datetime):
            from datetime import datetime, timezone
            created_at = datetime.now(timezone.utc)
        return cls(
            signal_id=str(data.get("signal_id") or str(uuid.uuid4())),
            bot_instance_id=str(data.get("bot_instance_id") or ""),
            symbol=str(data.get("symbol") or ""),
            direction=str(data.get("direction") or ""),
            confidence=float(data.get("confidence") or 0.0),
            wave_state=str(data.get("wave_state") or ""),
            entry_price=data.get("entry_price"),
            stop_loss=data.get("stop_loss"),
            take_profit=data.get("take_profit"),
            timeframe=str(data.get("timeframe") or "M5"),
            created_at=created_at,
            metadata=dict(data.get("metadata") or {}),
        )


class SignalBuilder:
    """Builds TradingSignal objects from raw engine analysis results."""

    def from_wave_analysis(
        self,
        bot_instance_id: str,
        symbol: str,
        wave_analysis: Any,
        entry_signal: Any = None,
    ) -> Optional[TradingSignal]:
        if wave_analysis is None:
            return None

        direction = ""
        if entry_signal and hasattr(entry_signal, "direction"):
            direction = entry_signal.direction
        elif hasattr(wave_analysis, "bias"):
            direction = wave_analysis.bias

        if not direction:
            return None

        signal = TradingSignal(
            bot_instance_id=bot_instance_id,
            symbol=symbol,
            direction=direction,
            confidence=getattr(wave_analysis, "confidence", 0.0),
            wave_state=str(getattr(wave_analysis, "main_wave", "")),
        )

        if entry_signal:
            signal.entry_price = getattr(entry_signal, "entry_price", None)
            signal.stop_loss = getattr(entry_signal, "stop_loss", None)
            signal.take_profit = getattr(entry_signal, "take_profit", None)

        return signal
