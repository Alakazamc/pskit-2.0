#!/usr/bin/env sh
set -eu

root_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
env_file="${1:-.env.docker}"
version="${PSKIT_VERSION:-0.3.0}"
case "$env_file" in
  /*) ;;
  *) env_file="$root_dir/$env_file" ;;
esac

if [ ! -f "$env_file" ]; then
  echo "Missing environment file: $env_file" >&2
  exit 2
fi

cd "$root_dir"
set -- docker compose --env-file "$env_file"
old_ifs="$IFS"
IFS=:
for compose_file in ${PSKIT_COMPOSE_FILES:-compose.yaml}; do
  set -- "$@" --file "$compose_file"
done
IFS="$old_ifs"
"$@" config --quiet
if [ "${PSKIT_EXPORT_SKIP_BUILD:-0}" = "1" ]; then
  echo "Using existing local application image (PSKIT_EXPORT_SKIP_BUILD=1)"
else
  "$@" build web worker
fi

qdrant_image="$("$@" config --images | awk '/^qdrant\/qdrant:/{print; exit}')"
if [ -z "$qdrant_image" ]; then
  echo "Could not resolve the Qdrant image from Compose" >&2
  exit 2
fi
if ! docker image inspect "$qdrant_image" >/dev/null 2>&1; then
  "$@" pull qdrant
fi

images="$("$@" config --images | sort -u)"
for image in $images; do
  docker image inspect "$image" >/dev/null
done

mkdir -p "$root_dir/dist"
bundle="$root_dir/dist/pskit2-${version}-linux-amd64-images.tar.gz"
checksum="$bundle.sha256"

docker save $images | gzip -9 >"$bundle"
(
  cd "$(dirname "$bundle")"
  sha256sum "$(basename "$bundle")" >"$(basename "$checksum")"
)

echo "bundle=$bundle"
echo "checksum=$checksum"
