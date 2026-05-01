from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BotInstance, DailyTradingState, ReconciliationQueueItem, Workspace


def _snapshot_dir() -> Path:
    import os

    raw = str(os.getenv("DR_SNAPSHOT_DIR") or "data/trading_brain/dr_snapshots").strip()
    path = Path(raw)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _json_default(value: Any):
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class SnapshotIntegrityError(ValueError):
    pass


def _snapshot_signing_secret() -> str:
    return str(os.getenv("DR_SNAPSHOT_SIGNING_KEY") or "").strip()


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=_json_default)


def _payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


class DisasterRecoveryService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_snapshot(self, *, include_runtime: bool = True) -> dict[str, Any]:
        snapshot_id = f"dr_{_now_utc().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

        workspaces = (
            (await self.db.execute(select(Workspace))).scalars().all()
        )
        bots = (
            (await self.db.execute(select(BotInstance))).scalars().all()
        )
        states = (
            (await self.db.execute(select(DailyTradingState))).scalars().all()
        )
        recon_pending = (
            (
                await self.db.execute(
                    select(ReconciliationQueueItem).where(
                        ReconciliationQueueItem.status.in_([
                            "pending",
                            "retry",
                            "failed_needs_operator",
                            "dead_letter",
                        ])
                    )
                )
            )
            .scalars()
            .all()
        )

        runtime = []
        if include_runtime:
            try:
                from app.core.registry import get_registry

                registry = get_registry()
                runtime = registry.list_all() if registry is not None and hasattr(registry, "list_all") else []
            except Exception:
                runtime = []

        payload = {
            "snapshot_id": snapshot_id,
            "created_at": _now_utc().isoformat(),
            "schema_version": "dr_snapshot_v1",
            "counts": {
                "workspaces": len(workspaces),
                "bots": len(bots),
                "daily_states": len(states),
                "reconciliation_pending": len(recon_pending),
                "runtime": len(runtime),
            },
            "workspaces": [
                {
                    "id": str(w.id),
                    "slug": str(w.slug),
                    "name": str(w.name),
                    "settings": dict(getattr(w, "settings", {}) or {}),
                    "updated_at": _json_default(getattr(w, "updated_at", None)),
                }
                for w in workspaces
            ],
            "bots": [
                {
                    "id": str(b.id),
                    "workspace_id": str(b.workspace_id),
                    "mode": str(b.mode),
                    "status": str(b.status),
                    "strategy_id": str(getattr(b, "strategy_id", "") or ""),
                    "broker_connection_id": str(getattr(b, "broker_connection_id", "") or ""),
                }
                for b in bots
            ],
            "daily_states": [
                {
                    "bot_instance_id": str(s.bot_instance_id),
                    "trading_day": str(getattr(s, "trading_day", "")),
                    "locked": bool(getattr(s, "locked", False)),
                    "lock_reason": str(getattr(s, "lock_reason", "") or ""),
                    "daily_loss_pct": float(getattr(s, "daily_loss_pct", 0.0) or 0.0),
                }
                for s in states
            ],
            "reconciliation_pending": [
                {
                    "id": int(q.id),
                    "bot_instance_id": str(q.bot_instance_id),
                    "idempotency_key": str(q.idempotency_key),
                    "status": str(q.status),
                    "attempts": int(q.attempts or 0),
                }
                for q in recon_pending
            ],
            "runtime": runtime,
        }

        secret = _snapshot_signing_secret()
        if not secret:
            raise SnapshotIntegrityError("snapshot_signing_key_missing")

        digest = _payload_hash(payload)
        signature = hmac.new(secret.encode("utf-8"), digest.encode("utf-8"), hashlib.sha256).hexdigest()
        envelope = {
            "snapshot_id": snapshot_id,
            "schema_version": "dr_snapshot_envelope_v1",
            "created_at": payload["created_at"],
            "integrity": {
                "payload_hash_sha256": digest,
                "signature_hmac_sha256": signature,
                "algorithm": "hmac_sha256",
            },
            "payload": payload,
        }

        path = _snapshot_dir() / f"{snapshot_id}.json"
        path.write_text(_canonical_json(envelope), encoding="utf-8")
        return {
            "snapshot_id": snapshot_id,
            "path": str(path),
            "counts": payload["counts"],
            "signed": True,
        }

    def list_snapshots(self, *, limit: int = 50) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        files = sorted(_snapshot_dir().glob("dr_*.json"), reverse=True)
        for fp in files[: max(1, min(int(limit), 200))]:
            stat = fp.stat()
            rows.append(
                {
                    "snapshot_id": fp.stem,
                    "path": str(fp),
                    "size_bytes": int(stat.st_size),
                    "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                }
            )
        return rows

    def load_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        path = _snapshot_dir() / f"{snapshot_id}.json"
        if not path.exists():
            raise FileNotFoundError(snapshot_id)
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise SnapshotIntegrityError("snapshot_invalid_format")

        envelope_payload = raw.get("payload")
        integrity = raw.get("integrity")
        if not isinstance(envelope_payload, dict) or not isinstance(integrity, dict):
            raise SnapshotIntegrityError("snapshot_unsigned_or_legacy_format")

        expected_hash = str(integrity.get("payload_hash_sha256") or "")
        actual_hash = _payload_hash(envelope_payload)
        if not expected_hash or not hmac.compare_digest(expected_hash, actual_hash):
            raise SnapshotIntegrityError("snapshot_payload_hash_mismatch")

        algorithm = str(integrity.get("algorithm") or "").lower()
        signature = str(integrity.get("signature_hmac_sha256") or "")
        if algorithm != "hmac_sha256" or not signature:
            raise SnapshotIntegrityError("snapshot_signature_metadata_invalid")

        secret = _snapshot_signing_secret()
        if not secret:
            raise SnapshotIntegrityError("snapshot_signing_key_missing")
        expected_signature = hmac.new(secret.encode("utf-8"), actual_hash.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected_signature):
            raise SnapshotIntegrityError("snapshot_signature_mismatch")

        return envelope_payload

    async def restore_snapshot(
        self,
        *,
        snapshot_id: str,
        dry_run: bool = True,
        restore_workspace_pause_flag: bool = True,
        stop_non_running_runtimes: bool = True,
    ) -> dict[str, Any]:
        payload = self.load_snapshot(snapshot_id)
        workspace_items = list(payload.get("workspaces") or [])
        bot_items = list(payload.get("bots") or [])

        planned_workspace_updates = 0
        applied_workspace_updates = 0
        planned_runtime_stops = 0
        applied_runtime_stops = 0

        if restore_workspace_pause_flag:
            for item in workspace_items:
                ws_id = str(item.get("id") or "")
                snapshot_settings = dict(item.get("settings") or {})
                if "portfolio_new_orders_paused" not in snapshot_settings:
                    continue
                planned_workspace_updates += 1
                if dry_run:
                    continue
                row = (
                    (
                        await self.db.execute(
                            select(Workspace).where(Workspace.id == ws_id).limit(1)
                        )
                    )
                    .scalar_one_or_none()
                )
                if row is None:
                    continue
                current = dict(getattr(row, "settings", {}) or {})
                current["portfolio_new_orders_paused"] = bool(snapshot_settings.get("portfolio_new_orders_paused", False))
                row.settings = current
                applied_workspace_updates += 1

        runtime_stop_ids: list[str] = []
        if stop_non_running_runtimes:
            runtime_by_bot = {
                str(item.get("id") or ""): str(item.get("status") or "")
                for item in bot_items
            }
            for bot_id, status in runtime_by_bot.items():
                if status.lower() in {"running", "paused"}:
                    continue
                runtime_stop_ids.append(bot_id)
            planned_runtime_stops = len(runtime_stop_ids)
            if not dry_run and runtime_stop_ids:
                try:
                    from app.core.registry import get_registry

                    registry = get_registry()
                    if registry is not None and hasattr(registry, "stop"):
                        for bot_id in runtime_stop_ids:
                            if registry.get(bot_id) is None:
                                continue
                            await registry.stop(bot_id)
                            applied_runtime_stops += 1
                except Exception:
                    pass

        if not dry_run:
            await self.db.commit()

        return {
            "snapshot_id": snapshot_id,
            "dry_run": bool(dry_run),
            "planned": {
                "workspace_pause_updates": planned_workspace_updates,
                "runtime_stops": planned_runtime_stops,
            },
            "applied": {
                "workspace_pause_updates": applied_workspace_updates,
                "runtime_stops": applied_runtime_stops,
            },
        }
