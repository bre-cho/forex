"""Reconciliation worker — syncs DB trade state with broker source of truth.

Runs periodically; compares open DB trades vs broker open positions; repairs
gaps; emits incidents on persistent mismatches. 
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

ReconciliationHook = Callable[[Dict[str, Any]], "asyncio.Coroutine[Any, Any, None]"]


@dataclass
class ReconciliationResult:
    bot_instance_id: str
    status: str  # ok | repaired | mismatch_persists | error
    open_positions_broker: int = 0
    open_positions_db: int = 0
    mismatches: List[Dict[str, Any]] = field(default_factory=list)
    repaired: int = 0
    error: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    finished_at: float = 0.0

    @property
    def latency_ms(self) -> float:
        return round((self.finished_at - self.started_at) * 1000.0, 2)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bot_instance_id": self.bot_instance_id,
            "status": self.status,
            "open_positions_broker": self.open_positions_broker,
            "open_positions_db": self.open_positions_db,
            "mismatches": self.mismatches,
            "repaired": self.repaired,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "latency_ms": self.latency_ms,
        }


class ReconciliationWorker:
    """
    Periodic background task that reconciles DB state with broker.

    Usage::

        worker = ReconciliationWorker(
            bot_instance_id="bot-123",
            provider=broker_provider,
            get_db_open_trades=my_fn,   # async fn → list of {id, broker_trade_id, ...}
            on_close_trade=close_fn,    # async fn(trade_id) → None
            on_result=result_fn,        # async fn(ReconciliationResult.to_dict()) → None
            on_incident=incident_fn,    # async fn(dict) → None
        )
        await worker.start()
        # later…
        await worker.stop()
    """

    def __init__(
        self,
        bot_instance_id: str,
        provider: Any,
        get_db_open_trades: Callable[[], "asyncio.Coroutine[Any, Any, List[Dict[str, Any]]]"],
        on_close_trade: Optional[Callable[[str], "asyncio.Coroutine[Any, Any, None]"]] = None,
        on_result: Optional[ReconciliationHook] = None,
        on_incident: Optional[ReconciliationHook] = None,
        interval_seconds: float = 30.0,
        max_mismatch_rounds: int = 3,
    ) -> None:
        self.bot_instance_id = bot_instance_id
        self.provider = provider
        self._get_db_open_trades = get_db_open_trades
        self._on_close_trade = on_close_trade
        self._on_result = on_result
        self._on_incident = on_incident
        self.interval_seconds = interval_seconds
        self.max_mismatch_rounds = max_mismatch_rounds
        self._task: Optional[asyncio.Task] = None
        self._mismatch_rounds: int = 0
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("ReconciliationWorker started: %s", self.bot_instance_id)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("ReconciliationWorker stopped: %s", self.bot_instance_id)

    async def run_once(self) -> ReconciliationResult:
        result = ReconciliationResult(bot_instance_id=self.bot_instance_id, status="ok")
        try:
            broker_positions = await self.provider.get_open_positions()
            db_trades = await self._get_db_open_trades()

            result.open_positions_broker = len(broker_positions)
            result.open_positions_db = len(db_trades)

            broker_ids = {str(p.get("id") or p.get("broker_order_id", "")) for p in broker_positions}
            db_ids = {str(t.get("broker_trade_id", "")) for t in db_trades}

            # DB says open but broker no longer has position → auto-close in DB
            stale_in_db = db_ids - broker_ids - {""}
            for trade_id_raw in stale_in_db:
                db_trade = next(
                    (t for t in db_trades if str(t.get("broker_trade_id", "")) == trade_id_raw),
                    None,
                )
                if db_trade and self._on_close_trade:
                    try:
                        await self._on_close_trade(str(db_trade.get("id", trade_id_raw)))
                        result.repaired += 1
                        result.mismatches.append({
                            "type": "db_trade_auto_closed",
                            "broker_trade_id": trade_id_raw,
                            "db_id": db_trade.get("id"),
                        })
                        logger.info(
                            "Reconciliation: auto-closed stale DB trade %s for bot %s",
                            trade_id_raw, self.bot_instance_id,
                        )
                    except Exception as exc:
                        logger.error("Reconciliation close failed %s: %s", trade_id_raw, exc)
                        result.mismatches.append({
                            "type": "close_error",
                            "broker_trade_id": trade_id_raw,
                            "error": str(exc),
                        })

            # Broker has positions DB doesn't know about (informational)
            ghost_at_broker = broker_ids - db_ids - {""}
            for broker_id in ghost_at_broker:
                result.mismatches.append({
                    "type": "broker_position_not_in_db",
                    "broker_id": broker_id,
                })

            if result.mismatches:
                self._mismatch_rounds += 1
                result.status = "repaired" if result.repaired > 0 else "mismatch_persists"
                if self._mismatch_rounds >= self.max_mismatch_rounds and self._on_incident:
                    incident = {
                        "bot_instance_id": self.bot_instance_id,
                        "incident_type": "reconciliation_mismatch_persists",
                        "severity": "critical",
                        "title": f"Reconciliation mismatch persists after {self._mismatch_rounds} rounds",
                        "detail": str(result.mismatches),
                    }
                    try:
                        await self._on_incident(incident)
                    except Exception as exc:
                        logger.error("on_incident hook failed: %s", exc)
            else:
                self._mismatch_rounds = 0
                result.status = "ok"

        except Exception as exc:
            result.status = "error"
            result.error = str(exc)
            logger.error("ReconciliationWorker error [%s]: %s", self.bot_instance_id, exc)

        result.finished_at = time.time()
        if self._on_result:
            try:
                await self._on_result(result.to_dict())
            except Exception as exc:
                logger.error("on_result hook failed: %s", exc)
        return result

    async def _loop(self) -> None:
        while self._running:
            await self.run_once()
            await asyncio.sleep(self.interval_seconds)
