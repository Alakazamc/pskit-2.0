#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${PSKIT_RUN_DIR:-/tmp/pskit2}"
BACKEND_PID="$RUN_DIR/backend.pid"
WORKER_PID="$RUN_DIR/worker.pid"
BACKEND_LOG="$RUN_DIR/backend.log"
WORKER_LOG="$RUN_DIR/worker.log"

mkdir -p "$RUN_DIR"

is_running() {
  local pid_file="$1"
  [ -s "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null
}

load_bind() {
  local requested_bind="${PSKIT_BIND:-}"
  if [ -f "$ROOT_DIR/.env" ]; then
    set -a
    # shellcheck source=/dev/null
    . "$ROOT_DIR/.env"
    set +a
  fi
  if [ -n "$requested_bind" ]; then
    PSKIT_BIND="$requested_bind"
  fi
  echo "${PSKIT_BIND:-${BIND_HOST:-127.0.0.1}:${BIND_PORT:-10706}}"
}

start_backend() {
  if is_running "$BACKEND_PID"; then
    echo "backend already running: $(cat "$BACKEND_PID")"
    return
  fi
  local bind
  bind="$(load_bind)"
  echo "starting backend on $bind"
  (
    cd "$ROOT_DIR"
    PSKIT_BIND="$bind" nohup scripts/start_backend.sh >"$BACKEND_LOG" 2>&1 &
    echo $! >"$BACKEND_PID"
  )
}

start_worker() {
  if is_running "$WORKER_PID"; then
    echo "worker already running: $(cat "$WORKER_PID")"
    return
  fi
  echo "starting worker"
  (
    cd "$ROOT_DIR"
    nohup scripts/run_worker_forever.sh >"$WORKER_LOG" 2>&1 &
    echo $! >"$WORKER_PID"
  )
}

stop_one() {
  local name="$1"
  local pid_file="$2"
  if ! is_running "$pid_file"; then
    echo "$name not running"
    rm -f "$pid_file"
    return
  fi
  local pid
  pid="$(cat "$pid_file")"
  echo "stopping $name: $pid"
  kill "$pid" 2>/dev/null || true
  for _ in 1 2 3 4 5; do
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$pid_file"
      return
    fi
    sleep 1
  done
  kill -TERM "$pid" 2>/dev/null || true
  rm -f "$pid_file"
}

status_one() {
  local name="$1"
  local pid_file="$2"
  if is_running "$pid_file"; then
    echo "$name running: $(cat "$pid_file")"
  else
    echo "$name stopped"
  fi
}

health() {
  local bind host port
  bind="$(load_bind)"
  host="${bind%:*}"
  port="${bind##*:}"
  curl -fsS --connect-timeout 3 "http://$host:$port/api/health" || true
  echo
}

case "${1:-status}" in
  start)
    start_backend
    start_worker
    ;;
  stop)
    stop_one worker "$WORKER_PID"
    stop_one backend "$BACKEND_PID"
    ;;
  restart)
    "$0" stop
    "$0" start
    ;;
  status)
    status_one backend "$BACKEND_PID"
    status_one worker "$WORKER_PID"
    health
    ;;
  logs)
    tail -n "${2:-120}" "$BACKEND_LOG" "$WORKER_LOG" 2>/dev/null || true
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|status|logs [lines]}" >&2
    exit 2
    ;;
esac
