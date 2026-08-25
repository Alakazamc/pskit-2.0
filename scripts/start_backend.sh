#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
REQUESTED_BIND="${PSKIT_BIND:-}"

if [ -f "$ROOT_DIR/.env" ]; then
  set -a
  # shellcheck source=/dev/null
  . "$ROOT_DIR/.env"
  set +a
fi
if [ -n "$REQUESTED_BIND" ]; then
  PSKIT_BIND="$REQUESTED_BIND"
fi

BIND="${PSKIT_BIND:-${BIND_HOST:-127.0.0.1}:${BIND_PORT:-10706}}"
HOST="${BIND%:*}"
PORT="${BIND##*:}"

cd "$BACKEND_DIR"
echo "Starting PSKit 2.0 backend on ${HOST}:${PORT}"
PYTHONPATH=. exec python3 -m uvicorn app.main:app --host "$HOST" --port "$PORT"
