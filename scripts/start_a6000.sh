#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "PSKit 2.0 A6000 startup"
echo "Root: $ROOT_DIR"
echo "Bind: ${PSKIT_BIND:-127.0.0.1:10706}"
echo
echo "Recommended process layout:"
echo "  Terminal 1: scripts/start_backend.sh"
echo "  Terminal 2: scripts/run_worker_forever.sh"
echo
exec "$ROOT_DIR/scripts/start_backend.sh"
