#!/usr/bin/env bash
set -Eeuo pipefail
agent_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "$agent_dir/../.." && pwd)
PYTHONPATH="$repo_dir${PYTHONPATH:+:$PYTHONPATH}" exec python \
  "$agent_dir/scripts/staging_preflight.py" "$@"
