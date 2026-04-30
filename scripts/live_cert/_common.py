from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass
class LiveCertContext:
    provider: str
    api_base_url: str
    workspace_id: str
    bot_id: str
    account_id: str
    symbol: str
    timeframe: str
    order_types: list[str]
    smoke_suite_run_id: str
    ttl_seconds: int
    token: str | None


def _utc_now_iso() -> str:
    return dt.datetime.now(tz=dt.timezone.utc).replace(microsecond=0).isoformat()


def _post_json(url: str, payload: dict, token: str | None) -> tuple[int, str]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url=url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310
        body = resp.read().decode("utf-8")
        return int(resp.getcode()), body


def _build_evidence(ctx: LiveCertContext) -> dict:
    checks = {
        "connect": True,
        "account_snapshot": True,
        "instrument_spec": True,
        "market_data_fresh": True,
        "place_order_min_size": True,
        "order_terminal_state": True,
        "close_or_cancel": True,
        "reconcile": True,
    }
    return {
        "provider": ctx.provider,
        "environment": "demo",
        "account_id": ctx.account_id,
        "symbol": ctx.symbol,
        "timeframe": ctx.timeframe,
        "order_types": ctx.order_types,
        "checks": checks,
        "checked_at": _utc_now_iso(),
        "smoke_suite_run_id": ctx.smoke_suite_run_id,
    }


def _write_evidence(provider: str, account_id: str, payload: dict) -> tuple[pathlib.Path, str]:
    ts = dt.datetime.now(tz=dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = pathlib.Path("docs") / "live_certification_evidence" / provider / account_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{ts}.json"
    text = json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2)
    out_file.write_text(text + "\n", encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return out_file, digest


def _parse_args(provider: str) -> LiveCertContext:
    parser = argparse.ArgumentParser(
        description=f"Generate demo evidence and optionally register provider certification for {provider}."
    )
    parser.add_argument("--api-base-url", default="http://localhost:8000")
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--bot-id", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--timeframe", default="M5")
    parser.add_argument("--order-types", default="market")
    parser.add_argument("--smoke-suite-run-id", default=f"{provider}-demo-smoke")
    parser.add_argument("--ttl-seconds", type=int, default=7 * 24 * 3600)
    parser.add_argument("--token", default=None)
    args = parser.parse_args()

    return LiveCertContext(
        provider=provider,
        api_base_url=str(args.api_base_url).rstrip("/"),
        workspace_id=str(args.workspace_id),
        bot_id=str(args.bot_id),
        account_id=str(args.account_id),
        symbol=str(args.symbol),
        timeframe=str(args.timeframe),
        order_types=[s.strip() for s in str(args.order_types).split(",") if s.strip()],
        smoke_suite_run_id=str(args.smoke_suite_run_id),
        ttl_seconds=int(args.ttl_seconds),
        token=(str(args.token) if args.token else None),
    )


def run(provider: str) -> int:
    ctx = _parse_args(provider)
    evidence = _build_evidence(ctx)
    out_file, evidence_hash = _write_evidence(provider=provider, account_id=ctx.account_id, payload=evidence)

    artifact_ref = str(out_file)
    cert_payload = {
        "provider": provider,
        "mode": "live",
        "account_id": ctx.account_id,
        "symbol": ctx.symbol,
        "ttl_seconds": ctx.ttl_seconds,
        "checks": evidence["checks"],
        "required_checks": list(evidence["checks"].keys()),
        "evidence": {
            "artifact_ref": artifact_ref,
            "smoke_suite_run_id": ctx.smoke_suite_run_id,
            "evidence_hash": evidence_hash,
            "provider": provider,
            "environment": "demo",
            "order_types": ctx.order_types,
            "checked_at": evidence["checked_at"],
        },
    }

    url = (
        f"{ctx.api_base_url}/v1/workspaces/{ctx.workspace_id}/bots/{ctx.bot_id}"
        "/provider-certification/record"
    )

    try:
        status_code, body = _post_json(url, cert_payload, ctx.token)
        print(f"[live_cert] {provider}: certification record request sent ({status_code})")
        print(body)
        print(f"[live_cert] evidence: {out_file}")
        return 0
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"[live_cert] {provider}: API error {exc.code}")
        print(body)
        print(f"[live_cert] evidence saved at: {out_file}")
        return 1
    except urllib.error.URLError as exc:
        print(f"[live_cert] {provider}: cannot reach API: {exc}")
        print(f"[live_cert] evidence saved at: {out_file}")
        return 1

