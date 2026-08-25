# PSKit 2.0 Docker Quick Start

This package is the reproducible onboarding path for PSKit 2.0. The default
stack contains the web/API process, the SQL-polling task worker, Qdrant,
bounded logs/resources, and persistent Docker volumes. Version 0.3.0 uses one
source tree for Web and Worker; the A6000 image is a derived science overlay.

## A6000 science worker

The core image is intentionally separate from the licensed BioAI runtime. On
an A6000 host with the legacy PSKit tree, model parameters, Foldseek and DSSP
already installed, start the science worker with the read-only overlay:

```bash
docker compose -f compose.yaml -f compose.science.a6000.example.yaml \
  --env-file .env.docker up -d --no-deps worker
```

The overlay expects these host paths by default and allows them to be changed
with `PSKIT_*_HOST` variables in `.env.docker`:

- `/data1/kxchen/pskit` (legacy INABe/PAIR runtime)
- `/data1/kxchen/pskit-data/model_parameters` (INABe, PAIR, ESM2, SaProt and RNA-FM weights)
- `/data1/kxchen/bin/foldseek` and `/data1/kxchen/bin/mkdssp`
- `/home/public/database/alphafold3` and `/home/public` (AF3 databases/parameters)

The standard overlay does not expose the host Docker socket. AlphaFold 3 in
the current legacy wrapper launches its image through Docker, so enabling it
requires the separately documented `compose.alphafold3.a6000.example.yaml`
overlay and an explicit security review:

```bash
docker compose -f compose.yaml \
  -f compose.science.a6000.example.yaml \
  -f compose.alphafold3.a6000.example.yaml \
  --env-file .env.docker up -d --no-deps worker
```

Never place provider keys or model weights in the repository. Keep them in
the server-only `.env.docker` file and mounted data directories.

## What is included

- Vue production frontend and FastAPI backend.
- Authentication, sessions, Agent UI, tool catalog, task center, and artifacts.
- The background worker used by the current PSKit 2.0 implementation.
- SQLite persistence shared by web and worker.
- Qdrant as an internal-only Compose service.
- The MCP Python SDK used by the optional remote RNA expert integration.

## What is not included

- API keys or the deployment `.env` file.
- Existing users, sessions, task outputs, or databases.
- PSKit 1.x runtime, model weights, AlphaFold 3 databases, or licensed images.
- Working CORAL, PepCCD, or remote RNA expert MCP services. Configure their
  SSE URLs only when the independently managed servers are reachable.

The omitted model/runtime data is several gigabytes and has independent
licenses. Keep it outside the image and mount it read-only for advanced use.

## Requirements

- Docker Engine 24 or later.
- Docker Compose v2.20 or later.
- About 4 GB free disk space for a first build and the Qdrant image.
- Internet access while building, unless an offline image bundle is supplied.

GPU is not required for the core stack.

## 1. Configure

Linux/macOS:

```bash
cp .env.docker.example .env.docker
chmod 600 .env.docker
```

PowerShell:

```powershell
Copy-Item .env.docker.example .env.docker
```

Edit `.env.docker`. `LLM_API_KEY` is required only for real Agent chat;
health, frontend, authentication, database-backed pages, and keyword fallback
can start without provider keys.

The default port is bound to `127.0.0.1:10716`. To expose it on a trusted LAN,
set `PSKIT_LISTEN_ADDRESS=0.0.0.0` and protect the service with a firewall and
HTTPS reverse proxy.

## 2. Build and start

```bash
docker compose --env-file .env.docker config --quiet
docker compose --env-file .env.docker build
docker compose --env-file .env.docker up -d --wait
```

Before production startup, set a random `INITIAL_ADMIN_BOOTSTRAP_TOKEN` of at
least 24 characters. Open <http://127.0.0.1:10716/agent>. Public registration
is enabled when `REGISTRATION_MODE=open`; the first administrator must submit
the bootstrap token. Administrators can then manage users at `/admin/users`.

## 3. Verify

```bash
./scripts/docker_smoke.sh http://127.0.0.1:10716
docker compose --env-file .env.docker ps
docker compose --env-file .env.docker logs --tail 100 web worker qdrant
```

PowerShell health check:

```powershell
Invoke-RestMethod http://127.0.0.1:10716/api/health
```

Expected response:

```json
{"ok":true,"service":"PSKit 2.0","version":"0.3.0"}
```

## Development loop

Edit source files, then rebuild only the application services:

```bash
docker compose --env-file .env.docker build web worker
docker compose --env-file .env.docker up -d --wait web worker
```

Follow logs:

```bash
docker compose --env-file .env.docker logs -f web worker
```

Run a shell in the image:

```bash
docker compose --env-file .env.docker exec web sh
```

## Persistence and reset

Normal stop/start keeps users, tasks, artifacts, and Qdrant data:

```bash
docker compose --env-file .env.docker down
docker compose --env-file .env.docker up -d --wait
```

The following command permanently deletes onboarding data and must be used only
for an intentional clean reset:

```bash
docker compose --env-file .env.docker down --volumes
```

## Legacy/GPU tools

The core image deliberately reports legacy model tools as unavailable. To add
them, create a separate derived GPU image containing the compatible PSKit 1.x
Python dependencies, then mount these host paths read-only:

- legacy source/runtime;
- model parameters;
- Foldseek and DSSP binaries;
- AlphaFold 3 databases and model parameters.

Do not mount `/var/run/docker.sock` into the public web container. Run AF3 or
other privileged model executors as a separate, narrowly scoped service.

## Offline handoff

On the build machine:

```bash
./scripts/export_docker_bundle.sh .env.docker
```

For a single-file handoff containing the sanitized source tree, Compose files,
documentation, and the offline images:

```bash
./scripts/export_offline_delivery.sh .env.docker
```

This creates `dist/pskit2-0.3.0-offline-delivery.tar` and its SHA-256 file.
The outer archive is intentionally uncompressed because its largest member is
already gzip-compressed.

If both images are already present and the build machine is offline, skip the
rebuild and export the verified local images:

```bash
PSKIT_EXPORT_SKIP_BUILD=1 ./scripts/export_docker_bundle.sh .env.docker
```

This creates a compressed multi-image archive and SHA-256 checksum under
`dist/`. It contains the PSKit image and pinned Qdrant image, but no volumes,
API keys, model weights, or user data.

On the recipient machine:

```bash
sha256sum -c pskit2-0.3.0-offline-delivery.tar.sha256
tar -xf pskit2-0.3.0-offline-delivery.tar
cd pskit2-0.3.0
cd dist
sha256sum -c pskit2-0.3.0-linux-amd64-images.tar.gz.sha256
gzip -dc pskit2-0.3.0-linux-amd64-images.tar.gz | docker load
cd ..
cp .env.docker.example .env.docker
docker compose --env-file .env.docker up -d --no-build --pull never --wait
```

Compose runs the database migration service before starting the web process.
Run `scripts/backup_runtime.sh` before upgrading an existing installation.
When upgrading from 0.1.0, the oldest account is promoted to administrator only
if the database does not already contain an administrator.

## Security checklist

- Never add `.env.docker`, keys, user databases, or model licenses to Git.
- Keep the default loopback port binding unless LAN access is required.
- Use HTTPS and `COOKIE_SECURE=true` for non-local deployments.
- Review every external model/tool license before distributing weights.
- Treat uploaded biological data and generated artifacts as private data.
