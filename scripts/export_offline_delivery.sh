#!/usr/bin/env sh
set -eu

root_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
env_file="${1:-.env.docker}"
version="${PSKIT_VERSION:-0.3.0}"
image_name="pskit2-${version}-linux-amd64-images.tar.gz"
image_bundle="$root_dir/dist/$image_name"
image_checksum="$image_bundle.sha256"
delivery="$root_dir/dist/pskit2-${version}-offline-delivery.tar"
delivery_checksum="$delivery.sha256"
file_list="$(mktemp /tmp/pskit-delivery-files.XXXXXX)"
raw_list="$(mktemp /tmp/pskit-delivery-raw.XXXXXX)"
manifest_dir="$(mktemp -d /tmp/pskit-delivery-manifest.XXXXXX)"

cleanup() {
  rm -f "$file_list" "$raw_list"
  rm -f "$manifest_dir/SOURCE_MANIFEST.txt"
  rmdir "$manifest_dir"
}
trap cleanup EXIT HUP INT TERM

if [ "${PSKIT_DELIVERY_REUSE_IMAGE_BUNDLE:-0}" = "1" ] \
  && [ -f "$image_bundle" ] \
  && [ -f "$image_checksum" ] \
  && (cd "$root_dir/dist" && sha256sum -c "$(basename "$image_checksum")" >/dev/null); then
  echo "Using existing verified image bundle (PSKIT_DELIVERY_REUSE_IMAGE_BUNDLE=1)"
else
  "$root_dir/scripts/export_docker_bundle.sh" "$env_file"
fi

cd "$root_dir"
if git rev-parse --show-toplevel >/dev/null 2>&1 \
  && [ -z "$(git rev-parse --show-prefix)" ]; then
  git -c core.quotepath=false ls-files >"$raw_list"
elif [ -f SOURCE_MANIFEST.txt ]; then
  # A prior offline delivery carries the same explicit source allowlist.
  cat SOURCE_MANIFEST.txt >"$raw_list"
else
  echo "Export requires a Git checkout or the original SOURCE_MANIFEST.txt" >&2
  exit 2
fi

sed 's,^\./,,' "$raw_list" | sort | while IFS= read -r path; do
  case "$path" in
    .env.example|.env.docker.example)
      ;;
    .env|.env.*|*/.env|*/.env.*|.runtime.env|*/.runtime.env|*.pem|*.key|*.sqlite|*.sqlite3|*.db|*.pth|*.pt|*.safetensors|*.onnx)
      continue
      ;;
    backend/data/*|data/*|tasks/*|artifacts/*|uploads/*|logs/*|model_parameters/*|references/*|secrets/*|*/secrets/*|backups/*|.runtime/*|.audit-data/*)
      continue
      ;;
    DEPLOYMENT.md|docs/*|scripts/start_a6000.sh)
      continue
      ;;
    .git/*|.github/.cache/*|backend/.venv/*|frontend/node_modules/*|frontend/dist/*|dist/*|.mypy_cache/*|.pytest_cache/*|.ruff_cache/*|*/__pycache__/*|*.pyc)
      continue
      ;;
  esac
  case "$path" in
    /*|../*|*/../*|SOURCE_MANIFEST.txt) continue ;;
  esac
  [ -f "$path" ] && [ ! -L "$path" ] || continue
  printf '%s\n' "$path"
done >"$file_list"

cp "$file_list" "$manifest_dir/SOURCE_MANIFEST.txt"
printf '%s\n' \
  "dist/$image_name" \
  "dist/$image_name.sha256" >>"$file_list"

tar -cf "$delivery" \
  --transform="s,^,pskit2-${version}/," \
  -T "$file_list" \
  -C "$manifest_dir" SOURCE_MANIFEST.txt

(
  cd "$(dirname "$delivery")"
  sha256sum "$(basename "$delivery")" >"$(basename "$delivery_checksum")"
)

echo "delivery=$delivery"
echo "checksum=$delivery_checksum"
