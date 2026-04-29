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
    account_equity: float = 0.0
    account_balance: float = 0.0
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
            "account_equity": self.account_equity,
            "account_balance": self.account_balance,
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
        get_unknown_order_attempts: Optional[Callable] = None,
        on_unknown_resolved: Optional[Callable] = None,
    ) -> None:
        self.bot_instance_id = bot_instance_id
        self.provider = provider
        self._get_db_open_trades = get_db_open_trades
        self._on_close_trade = on_close_trade
        self._on_result = on_result
        self._on_incident = on_incident
        self.interval_seconds = interval_seconds
        self.max_mismatch_rounds = max_mismatch_rounds
        self._get_unknown_order_attempts = get_unknown_order_attempts
        self._on_unknown_resolved = on_unknown_resolved
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
            # Broker account sync snapshot for daily-state recompute
            account_info_fn = getattr(self.provider, "get_account_info", None)
            if callable(account_info_fn):
                try:
                    info = await account_info_fn()
                    result.account_equity = float(getattr(info, "equity", 0.0) or 0.0)
                    result.account_balance = float(getattr(info, "balance", 0.0) or 0.0)
                except Exception as exc:
                    logger.warning("Reconciliation account_info failed [%s]: %s", self.bot_instance_id, exc)

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

            # Broker has positions DB doesn't know about — P0.6: this is NOT informational
            # In live mode: pause new orders immediately + critical incident
            ghost_at_broker = broker_ids - db_ids - {""}
            for broker_id in ghost_at_broker:
                result.mismatches.append({
                    "type": "broker_ghost_position",
                    "broker_id": broker_id,
                    "severity": "critical",
                    "action": "pause_new_orders",
                })
                logger.error(
                    "Ghost broker position detected [%s]: broker_id=%s — pausing new orders",
                    self.bot_instance_id, broker_id,
                )
                if self._on_incident:
                    ghost_incident = {
                        "bot_instance_id": self.bot_instance_id,
                        "incident_type": "broker_ghost_position",
                        "severity": "critical",
                        "title": f"Broker ghost position not in DB: {broker_id}",
                        "detail": f"broker_id={broker_id}; DB has no matching trade",
                        "escalation_action": "pause_new_orders",
                        "broker_position_id": broker_id,
                    }
                    try:
                        await self._on_incident(ghost_incident)
                    except Exception as exc:
                        logger.error("on_incident(ghost_position) failed: %s", exc)

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
                        "escalation_action": "kill_switch",
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
            if self._on_incident:
                incident = {
                    "bot_instance_id": self.bot_instance_id,
                    "incident_type": "reconciliation_runtime_error",
                    "severity": "critical",
                    "title": "Reconciliation worker error",
                    "detail": str(exc),
                    "escalation_action": "kill_switch",
                }
                try:
                    await self._on_incident(incident)
                except Exception as incident_exc:
                    logger.error("on_incident hook failed after reconciliation error: %s", incident_exc)

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
            await self.resolve_unknown_orders()
            await asyncio.sleep(self.interval_seconds)

    async def resolve_unknown_orders(self) -> None:
        """Scan and resolve any UNKNOWN broker_order_attempts via UnknownOrderReconciler.

        This method is a no-op if ``get_unknown_order_attempts`` hook is not provided.
        ``get_unknown_order_attempts`` must be an async callable that returns a list of
        dicts with at least ``idempotency_key`` and optionally ``signal_id``.

        ``on_unknown_resolved`` hook (if provided) receives the UnknownOrderResult dict
        and should update DB state (attempt transitions, projection, incidents).
        """
        get_unknowns = getattr(self, "_get_unknown_order_attempts", None)
        on_resolved = getattr(self, "_on_unknown_resolved", None)
        if not callable(get_unknowns):
            return
        try:
            unknown_orders = await get_unknowns()
        except Exception as exc:
            logger.warning("get_unknown_order_attempts failed for %s: %s", self.bot_instance_id, exc)
            return
        if not unknown_orders:
            return

        from execution_service.unknown_order_reconciler import UnknownOrderReconciler
        reconciler = UnknownOrderReconciler(
            provider=self.provider,
            on_resolved=on_resolved,
            max_retries=1,  # worker runs periodically; each sweep does 1 attempt
            retry_interval_seconds=0,
        )
        for order in unknown_orders:
            key = str(order.get("idempotency_key") or "")
            if not key:
                continue
            try:
                result = await reconciler.resolve_unknown_order(
                    bot_instance_id=self.bot_instance_id,
                    idempotency_key=key,
                    signal_id=str(order.get("signal_id") or ""),
                )
                if result.outcome == "failed_needs_operator" and self._on_incident:
                    await self._on_incident({
                        "type": "unknown_order_failed_needs_operator",
                        "bot_instance_id": self.bot_instance_id,
                        "idempotency_key": key,
                        "detail": result.error or "max_retries_exceeded",
                        "severity": "critical",
                        "escalation_action": "operator_required",
                    })
            except Exception as exc:
                logger.error("resolve_unknown_orders error key=%s: %s", key, exc)

