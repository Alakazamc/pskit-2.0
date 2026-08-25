#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
HOST="${PSKIT_SMOKE_HOST:-127.0.0.1}"
PORT="${PSKIT_SMOKE_PORT:-18076}"
LOG_FILE="${PSKIT_SMOKE_LOG:-/tmp/pskit2-live-smoke.log}"

cd "$BACKEND_DIR"
PYTHONPATH=. python3 -m uvicorn app.main:app --host "$HOST" --port "$PORT" >"$LOG_FILE" 2>&1 &
PID=$!

cleanup() {
  kill "$PID" >/dev/null 2>&1 || true
  wait "$PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT

for _ in $(seq 1 30); do
  if curl -fsS "http://$HOST:$PORT/api/health" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

curl -fsS "http://$HOST:$PORT/api/health"
printf '\n'
if [ -f "$ROOT_DIR/frontend/dist/index.html" ]; then
  curl -fsS "http://$HOST:$PORT/" | grep -q "PSKit 2.0"
else
  echo "frontend/dist not found; skipped SPA check"
fi
echo "smoke_live_backend: ok"
