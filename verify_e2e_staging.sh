#!/usr/bin/env bash
# verify_e2e_staging.sh — End-to-end staging verification.
# Prints GO / NO-GO after automatically:
#   1. Registering a user
#   2. Logging in and obtaining a token
#   3. Creating a workspace
#   4. Creating a paper broker connection
#   5. Creating a bot
#   6. Starting the bot
#   7. Smoke-testing the WebSocket
#
# Usage:
#   ./verify_e2e_staging.sh
#
# Environment overrides (optional):
#   BASE_URL           — HTTP base URL  (default: http://127.0.0.1:8000)
#   WS_BASE_URL        — WebSocket base URL (default: ws://127.0.0.1:8000)
#   SKIP_SETUP         — set to 1 to skip venv/docker/migration/test steps
#                        (useful when the stack is already running)
#   TEST_PASSWORD      — password for the ephemeral staging user
#                        (default: randomly generated 24-char string)
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API_DIR="$ROOT_DIR/apps/api"
COMPOSE_FILE="$ROOT_DIR/infra/docker/docker-compose.dev.yml"
VENV_DIR="$ROOT_DIR/.venv"
LOG_DIR="$ROOT_DIR/.verify"
API_LOG="$LOG_DIR/api.log"
BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
WS_BASE_URL="${WS_BASE_URL:-ws://127.0.0.1:8000}"
SKIP_SETUP="${SKIP_SETUP:-0}"

# Tunable constants
MAX_POLL_ATTEMPTS=40   # seconds to wait for the API health check
WS_TIMEOUT=10          # seconds to wait for a WebSocket response

mkdir -p "$LOG_DIR"

# ── Helpers ───────────────────────────────────────────────────────────────────

pass() { echo "✅  $1"; }
info() { echo "•  $1"; }
fail() { echo "❌  $1"; echo ""; echo "🔴 NO-GO"; exit 1; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "Required command not found: '$1'"
}

cleanup() {
  if [[ -n "${API_PID:-}" ]] && kill -0 "$API_PID" 2>/dev/null; then
    kill "$API_PID" || true
    wait "$API_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

# extract_json <json_string> <dot.separated.key>
# Exits with code 2 if the key is missing or null.
extract_json() {
  local json="$1"
  local key="$2"
  python3 - "$json" "$key" <<'PYEOF'
import json, sys
data = json.loads(sys.argv[1])
value = data
for part in sys.argv[2].split('.'):
    if isinstance(value, dict):
        value = value.get(part)
    else:
        value = None
        break
if value is None:
    sys.exit(2)
if isinstance(value, (dict, list)):
    print(json.dumps(value))
else:
    print(value)
PYEOF
}

# http_json <METHOD> <URL> [<body>] [<token>]
http_json() {
  local method="$1"
  local url="$2"
  local body="${3:-}"
  local auth_token="${4:-}"

  local headers=(-H "Content-Type: application/json")
  if [[ -n "$auth_token" ]]; then
    headers+=(-H "Authorization: Bearer $auth_token")
  fi

  if [[ -n "$body" ]]; then
    curl -fsS -X "$method" "${headers[@]}" "$url" -d "$body"
  else
    curl -fsS -X "$method" "${headers[@]}" "$url"
  fi
}

wait_for_http() {
  local url="$1"
  info "Polling $url ..."
  for _ in $(seq 1 "$MAX_POLL_ATTEMPTS"); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

smoke_test_ws() {
  local ws_url="$1"

  if command -v websocat >/dev/null 2>&1; then
    # Send "ping"; the server replies with a JSON pong event.
    echo "ping" | timeout "${WS_TIMEOUT}s" websocat "$ws_url" \
      >"$LOG_DIR/ws.out" 2>"$LOG_DIR/ws.err" || true
  elif command -v wscat >/dev/null 2>&1; then
    timeout "${WS_TIMEOUT}s" wscat --execute "ping" -c "$ws_url" \
      >"$LOG_DIR/ws.out" 2>"$LOG_DIR/ws.err" || true
  else
    fail "websocat or wscat is required for the WebSocket smoke test"
  fi

  # Success: received content (pong/event) or stderr shows a clean connection.
  if grep -Eiq "pong|event|message" "$LOG_DIR/ws.out" 2>/dev/null; then
    return 0
  fi
  if grep -Eiq "connected|open|established" "$LOG_DIR/ws.err" 2>/dev/null; then
    return 0
  fi
  return 1
}

# ── Pre-flight checks ─────────────────────────────────────────────────────────

need_cmd python3
need_cmd curl
need_cmd docker
need_cmd make

# ── Environment setup (skippable) ─────────────────────────────────────────────

if [[ "$SKIP_SETUP" != "1" ]]; then
  need_cmd pip

  info "Creating virtualenv"
  python3 -m venv "$VENV_DIR" || fail "Could not create virtualenv"
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate" 2>/dev/null \
    || source "$VENV_DIR/Scripts/activate" \
    || fail "Could not activate virtualenv"

  info "Upgrading pip"
  python3 -m pip install -U pip >/dev/null || fail "pip upgrade failed"

  info "Installing dependencies"
  pip install -r "$ROOT_DIR/requirements-test.txt" >/dev/null \
    || fail "requirements-test.txt install failed"
  pip install -e "$ROOT_DIR/apps/api" >/dev/null \
    || fail "apps/api editable install failed"
  pip install -e "$ROOT_DIR/services/trading-core" >/dev/null \
    || fail "services/trading-core editable install failed"
  pass "Dependencies OK"

  info "Starting Postgres + Redis"
  docker compose -f "$COMPOSE_FILE" up -d || fail "docker compose up failed"
  pass "Docker services OK"

  info "Running migrations"
  (cd "$API_DIR" && alembic upgrade head) >/dev/null || fail "Migration failed"
  pass "Migration OK"

  info "Running test suite"
  (cd "$ROOT_DIR" && make test) || fail "Tests failed"
  pass "Tests OK"

  info "Booting API server"
  (cd "$API_DIR" && uvicorn app.main:app --host 127.0.0.1 --port 8000 \
    >"$API_LOG" 2>&1) &
  API_PID=$!

  info "Waiting for API to become ready"
  wait_for_http "$BASE_URL/health" || fail "API did not start. See log: $API_LOG"
  pass "API boot OK"
fi

# ── Test data ─────────────────────────────────────────────────────────────────

TS="$(date +%s)"
TEST_EMAIL="staging.${TS}@example.com"
# Generate a random password unless the caller supplies one explicitly.
TEST_PASSWORD="${TEST_PASSWORD:-$(python3 -c "import secrets,string; \
  chars=string.ascii_letters+string.digits+'!@#'; \
  print(secrets.token_urlsafe(16)[:16] + secrets.choice(string.digits) \
        + secrets.choice('!@#') + secrets.choice(string.ascii_uppercase))")}"
TEST_NAME="Staging Verify User"
WORKSPACE_SLUG="staging-ws-${TS}"
WORKSPACE_NAME="Staging Workspace ${TS}"
BROKER_NAME="Paper Broker ${TS}"
BOT_NAME="Paper Bot ${TS}"

# ── 1. Register ───────────────────────────────────────────────────────────────

info "Registering user ($TEST_EMAIL)"
REGISTER_PAYLOAD=$(python3 -c "
import json, sys
print(json.dumps({'email': sys.argv[1], 'password': sys.argv[2], 'full_name': sys.argv[3]}))
" "$TEST_EMAIL" "$TEST_PASSWORD" "$TEST_NAME")

REGISTER_RESP=$(http_json POST "$BASE_URL/v1/auth/register" "$REGISTER_PAYLOAD") \
  || fail "User registration failed"
USER_ID=$(extract_json "$REGISTER_RESP" "id") || fail "Could not read user id from register response"
pass "Register OK (id=$USER_ID)"

# ── 2. Login ──────────────────────────────────────────────────────────────────

info "Logging in"
LOGIN_PAYLOAD=$(python3 -c "
import json, sys
print(json.dumps({'email': sys.argv[1], 'password': sys.argv[2]}))
" "$TEST_EMAIL" "$TEST_PASSWORD")

LOGIN_RESP=$(http_json POST "$BASE_URL/v1/auth/login" "$LOGIN_PAYLOAD") \
  || fail "Login failed"
TOKEN=$(extract_json "$LOGIN_RESP" "access_token") || fail "Could not read access_token from login response"
pass "Login OK"

# ── 3. Create workspace ───────────────────────────────────────────────────────

info "Creating workspace ($WORKSPACE_NAME)"
WS_PAYLOAD=$(python3 -c "
import json, sys
print(json.dumps({'name': sys.argv[1], 'slug': sys.argv[2]}))
" "$WORKSPACE_NAME" "$WORKSPACE_SLUG")

WS_RESP=$(http_json POST "$BASE_URL/v1/workspaces" "$WS_PAYLOAD" "$TOKEN") \
  || fail "Workspace creation failed"
WS_ID=$(extract_json "$WS_RESP" "id") || fail "Could not read workspace id from response"
pass "Workspace OK (id=$WS_ID)"

# ── 4. Create paper broker connection ─────────────────────────────────────────

info "Creating paper broker ($BROKER_NAME)"
BROKER_PAYLOAD=$(python3 -c "
import json, sys
print(json.dumps({'name': sys.argv[1], 'broker_type': 'paper', 'credentials': {}}))
" "$BROKER_NAME")

BROKER_RESP=$(http_json POST "$BASE_URL/v1/workspaces/$WS_ID/broker-connections" \
  "$BROKER_PAYLOAD" "$TOKEN") || fail "Broker connection creation failed"
BROKER_ID=$(extract_json "$BROKER_RESP" "id") || fail "Could not read broker id from response"
pass "Broker OK (id=$BROKER_ID)"

# ── 5. Create bot ─────────────────────────────────────────────────────────────

info "Creating bot ($BOT_NAME)"
BOT_PAYLOAD=$(python3 -c "
import json, sys
print(json.dumps({'name': sys.argv[1], 'symbol': 'EURUSD', 'timeframe': 'M5', 'mode': 'paper'}))
" "$BOT_NAME")

BOT_RESP=$(http_json POST "$BASE_URL/v1/workspaces/$WS_ID/bots" \
  "$BOT_PAYLOAD" "$TOKEN") || fail "Bot creation failed"
BOT_ID=$(extract_json "$BOT_RESP" "id") || fail "Could not read bot id from response"
pass "Bot OK (id=$BOT_ID)"

# ── 6. Start bot ──────────────────────────────────────────────────────────────

info "Starting bot"
START_RESP=$(http_json POST "$BASE_URL/v1/workspaces/$WS_ID/bots/$BOT_ID/start" "" "$TOKEN") \
  || fail "Bot start request failed"
BOT_STATUS=$(extract_json "$START_RESP" "status") || fail "Could not read status from start response"
[[ "$BOT_STATUS" == "running" ]] \
  || fail "Bot is not in 'running' state after start (status=$BOT_STATUS)"
pass "Start bot OK (status=$BOT_STATUS)"

# ── 7. WebSocket smoke test ───────────────────────────────────────────────────

info "WebSocket smoke test"
# Token is passed as a query parameter (see ws.py _extract_bearer_token)
WS_URL="${WS_BASE_URL}/ws/bots/${BOT_ID}?token=${TOKEN}"
if smoke_test_ws "$WS_URL"; then
  pass "WebSocket OK"
else
  fail "WebSocket smoke test failed. Logs: $LOG_DIR/ws.out, $LOG_DIR/ws.err"
fi

# ── Result ────────────────────────────────────────────────────────────────────

echo ""
echo "🟢 GO — all verification steps passed."
