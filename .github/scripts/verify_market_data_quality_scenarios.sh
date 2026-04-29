#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."
PYTHONPATH=/workspaces/forex:services/trading-core:services/execution-service \
  pytest -q services/trading-core/tests/test_market_data_quality.py

echo "[verify_market_data_quality_scenarios] OK"
