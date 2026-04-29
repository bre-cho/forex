#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

errors=0

# 1) Live runtime must hard-fail when broker instrument spec missing.
if ! grep -Eq 'instrument_spec_missing_live|instrument_spec_fetch_failed' services/trading-core/trading_core/runtime/bot_runtime.py; then
  echo "[verify_live_no_fallback_spec] missing live hard-fail for instrument spec"
  errors=$((errors+1))
fi

# 2) RiskContextBuilder must fail closed when live spec is absent.
if ! grep -Eq 'risk_context_missing_instrument_spec' services/trading-core/trading_core/risk/risk_context_builder.py; then
  echo "[verify_live_no_fallback_spec] RiskContextBuilder missing fail-closed guard"
  errors=$((errors+1))
fi

# 3) Live path must pass runtime_mode into risk builder (no silent paper fallback).
if ! grep -Eq 'runtime_mode=self\.runtime_mode' services/trading-core/trading_core/runtime/bot_runtime.py; then
  echo "[verify_live_no_fallback_spec] bot_runtime does not pass runtime_mode to risk context"
  errors=$((errors+1))
fi

# 4) PreExecutionGate must block non-live providers in live runtime.
if ! grep -Eq 'provider_not_live_capable' services/trading-core/trading_core/runtime/pre_execution_gate.py; then
  echo "[verify_live_no_fallback_spec] pre_execution_gate missing provider live-capability block"
  errors=$((errors+1))
fi

if [[ "$errors" -gt 0 ]]; then
  echo "[verify_live_no_fallback_spec] FAILED with $errors issue(s)"
  exit 1
fi

echo "[verify_live_no_fallback_spec] OK"
