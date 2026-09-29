#!/usr/bin/env bash
set -Eeuo pipefail

root_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${1:-$root_dir/.env.docker}"
backup_root="${PSKIT_BACKUP_ROOT:-$root_dir/backups}"
retention_days="${PSKIT_BACKUP_RETENTION_DAYS:-14}"

if [[ ! -f "$env_file" ]]; then
  echo "Missing environment file: $env_file" >&2
  exit 2
fi
backup_root="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$backup_root")"
case "$backup_root" in
  /|/home|/data1|"")
    echo "Refusing unsafe backup root: $backup_root" >&2
    exit 2
    ;;
esac

mkdir -p "$backup_root"
chmod 700 "$backup_root"
exec 8>"$backup_root/.backup.lock"
if ! flock -n 8; then
  echo "Another runtime backup is already running" >&2
  exit 3
fi
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
destination="$backup_root/$stamp"
# Refuse to overwrite an existing completed backup from the same second.
mkdir "$destination"
mkdir "$destination/config"
chmod 700 "$backup_root" "$destination" "$destination/config"
backup_complete=0
web_id=""
container_backup_path=""
cleanup_incomplete_backup() {
  if [[ -n "$web_id" && -n "$container_backup_path" ]]; then
    docker exec "$web_id" rm -f -- "$container_backup_path" >/dev/null 2>&1 || true
  fi
  if (( backup_complete == 0 )) && [[ -d "$destination" ]]; then
    case "$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$destination")" in
      "$backup_root"/20??????T??????Z) rm -rf -- "$destination" ;;
      *) echo "Refusing to clean unexpected backup path: $destination" >&2 ;;
    esac
  fi
}
trap cleanup_incomplete_backup EXIT INT TERM

compose=(docker compose --env-file "$env_file")
IFS=':' read -r -a compose_files <<< "${PSKIT_COMPOSE_FILES:-compose.yaml}"
for file in "${compose_files[@]}"; do
  [[ "$file" = /* ]] || file="$root_dir/$file"
  compose+=(--file "$file")
  cp -- "$file" "$destination/config/"
done
cp -- "$env_file" "$destination/config/.env.docker"
chmod 600 "$destination/config/.env.docker"

# Preserve server-only configuration (for example MCP/bootstrap secrets) without
# baking it into an image or committing it to the repository. Paths are
# resolved relative to the runtime directory and copied with restrictive modes.
IFS=':' read -r -a extra_config_files <<< "${PSKIT_EXTRA_CONFIG_FILES:-}"
for file in "${extra_config_files[@]}"; do
  [[ -n "$file" ]] || continue
  [[ "$file" = /* ]] || file="$root_dir/$file"
  if [[ ! -f "$file" ]]; then
    echo "Missing extra backup config: $file" >&2
    exit 2
  fi
  cp -- "$file" "$destination/config/$(basename -- "$file")"
  chmod 600 "$destination/config/$(basename -- "$file")"
done

web_id="$(cd "$root_dir" && "${compose[@]}" ps -q web)"
if [[ -z "$web_id" ]]; then
  echo "PSKit web container is not available" >&2
  exit 3
fi

container_backup_path="$(docker exec -i "$web_id" python - <<'PY'
import os
import sqlite3
import tempfile
from pathlib import Path

url = os.environ["DATABASE_URL"]
if not url.startswith("sqlite:///"):
    raise SystemExit("backup_runtime.sh currently expects the deployed SQLite database")
source = Path(url.removeprefix("sqlite:///"))
descriptor, filename = tempfile.mkstemp(prefix=".pskit-backup-", suffix=".sqlite3", dir=os.environ["DATA_DIR"])
os.close(descriptor)
target = Path(filename)
with sqlite3.connect(source) as source_db, sqlite3.connect(target) as target_db:
    source_db.backup(target_db)
    if target_db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise SystemExit("SQLite backup quick_check failed")
print(target)
PY
)"
docker exec "$web_id" cat -- "$container_backup_path" \
  > "$destination/pskit2.sqlite3"
docker exec "$web_id" rm -f -- "$container_backup_path"
container_backup_path=""

docker exec "$web_id" sh -c 'tar -C "$ARTIFACT_DIR" -czf - .' \
  > "$destination/artifacts.tar.gz"

# Stream snapshot bytes to the host; /tmp in the web container is only 512 MB.
# Small metadata is kept separately so stdout remains a binary-only stream.
docker exec -i "$web_id" python - /tmp/pskit2-qdrant.json > "$destination/qdrant.snapshot" <<'PY'
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote

import httpx

metadata_path = Path(sys.argv[1])
base = os.environ["QDRANT_URL"].rstrip("/")
public_name = os.environ.get("QDRANT_COLLECTION", "pskit_knowledge")
with httpx.Client(timeout=120) as client:
    aliases_response = client.get(f"{base}/aliases")
    aliases_response.raise_for_status()
    aliases = {
        item["alias_name"]: item["collection_name"]
        for item in aliases_response.json().get("result", {}).get("aliases", [])
    }
    collection = aliases.get(public_name, public_name)
    probe = client.get(f"{base}/collections/{quote(collection, safe='')}")
    if probe.status_code == 404:
        metadata_path.write_text(
            json.dumps({"public_name": public_name, "collection": collection, "present": False}),
            encoding="utf-8",
        )
        raise SystemExit(0)
    probe.raise_for_status()
    created = client.post(f"{base}/collections/{quote(collection, safe='')}/snapshots")
    created.raise_for_status()
    name = created.json()["result"]["name"]
    snapshot_url = f"{base}/collections/{quote(collection, safe='')}/snapshots/{quote(name, safe='')}"
    try:
        with client.stream("GET", snapshot_url) as downloaded:
            downloaded.raise_for_status()
            for chunk in downloaded.iter_bytes():
                sys.stdout.buffer.write(chunk)
        metadata_path.write_text(
            json.dumps({"public_name": public_name, "collection": collection, "present": True, "snapshot": name}),
            encoding="utf-8",
        )
    finally:
        client.delete(snapshot_url).raise_for_status()
PY
docker exec "$web_id" sh -c 'cat /tmp/pskit2-qdrant.json' \
  > "$destination/qdrant.json"
docker exec "$web_id" rm -f /tmp/pskit2-qdrant.json

(
  cd "$destination"
  sha256sum pskit2.sqlite3 artifacts.tar.gz qdrant.snapshot qdrant.json > SHA256SUMS
  sha256sum -c SHA256SUMS
)

if [[ "$retention_days" =~ ^[0-9]+$ ]] && (( retention_days > 0 )); then
  find "$backup_root" -mindepth 1 -maxdepth 1 -type d \
    -name '20??????T??????Z' -mtime "+$retention_days" -exec rm -rf -- {} +
fi

backup_complete=1
echo "backup=$destination"
