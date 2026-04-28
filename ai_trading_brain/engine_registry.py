from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional


@dataclass
class EngineHandle:
    name: str
    engine: Any
    critical: bool = False
    health_fn: Optional[Callable[[Any], Dict[str, Any]]] = None

    def health(self) -> Dict[str, Any]:
        try:
            if self.health_fn:
                return {"name": self.name, "ok": True, **self.health_fn(self.engine)}
            return {"name": self.name, "ok": self.engine is not None, "mode": "ready" if self.engine else "missing"}
        except Exception as exc:  # noqa: BLE001
            return {"name": self.name, "ok": False, "mode": "error", "error": str(exc), "critical": self.critical}


@dataclass
class TradingEngineRegistry:
    """Single registry for all advanced engines; prevents isolated bot behavior."""

    handles: Dict[str, EngineHandle] = field(default_factory=dict)

    def register(self, name: str, engine: Any, *, critical: bool = False,
                 health_fn: Optional[Callable[[Any], Dict[str, Any]]] = None) -> None:
        self.handles[name] = EngineHandle(name=name, engine=engine, critical=critical, health_fn=health_fn)

    def get(self, name: str, default: Any = None) -> Any:
        handle = self.handles.get(name)
        return handle.engine if handle else default

    def health(self) -> Dict[str, Any]:
        rows = {name: handle.health() for name, handle in sorted(self.handles.items())}
        critical_failed = [name for name, row in rows.items() if row.get("critical") and not row.get("ok")]
        return {
            "ok": not critical_failed,
            "critical_failed": critical_failed,
            "engines": rows,
            "count": len(rows),
        }
