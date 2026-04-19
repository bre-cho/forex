"""shared_schemas — base event shapes shared across services."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional


@dataclass
class BaseEvent:
    """Base shape for all platform events."""
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str = ""
    source_service: str = ""
    bot_instance_id: Optional[str] = None
    workspace_id: Optional[str] = None
    user_id: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    payload: Dict[str, Any] = field(default_factory=dict)
    schema_version: str = "1.0"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "source_service": self.source_service,
            "bot_instance_id": self.bot_instance_id,
            "workspace_id": self.workspace_id,
            "user_id": self.user_id,
            "timestamp": self.timestamp.isoformat(),
            "payload": self.payload,
            "schema_version": self.schema_version,
        }


@dataclass
class BotStatusEvent(BaseEvent):
    event_type: str = "bot.status_changed"

    @classmethod
    def create(cls, bot_instance_id: str, status: str, **payload) -> "BotStatusEvent":
        return cls(
            bot_instance_id=bot_instance_id,
            payload={"status": status, **payload},
        )


@dataclass
class TradeEvent(BaseEvent):
    event_type: str = "trade.executed"

    @classmethod
    def create(cls, bot_instance_id: str, trade_data: Dict[str, Any]) -> "TradeEvent":
        return cls(bot_instance_id=bot_instance_id, payload=trade_data)


@dataclass
class SignalEvent(BaseEvent):
    event_type: str = "signal.generated"

    @classmethod
    def create(cls, bot_instance_id: str, signal_data: Dict[str, Any]) -> "SignalEvent":
        return cls(bot_instance_id=bot_instance_id, payload=signal_data)
