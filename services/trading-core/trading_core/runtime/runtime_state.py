from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Dict, Any
import time


class RuntimeStatus(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"


@dataclass
class RuntimeState:
    bot_instance_id: str
    status: RuntimeStatus = RuntimeStatus.STOPPED
    started_at: Optional[float] = None
    stopped_at: Optional[float] = None
    balance: float = 0.0
    equity: float = 0.0
    daily_pnl: float = 0.0
    open_trades: int = 0
    total_trades: int = 0
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bot_instance_id": self.bot_instance_id,
            "status": self.status.value,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "balance": self.balance,
            "equity": self.equity,
            "daily_pnl": self.daily_pnl,
            "open_trades": self.open_trades,
            "total_trades": self.total_trades,
            "error_message": self.error_message,
            "metadata": dict(self.metadata),
            "uptime_seconds": (
                (time.time() - self.started_at)
                if self.started_at and self.status == RuntimeStatus.RUNNING
                else 0.0
            ),
        }
