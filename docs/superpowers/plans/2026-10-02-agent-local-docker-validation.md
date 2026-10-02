# Research Agent Local Docker Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and verify the new React/Python/Pi stack in local Docker without changing the running WSL or A6000 services.

**Architecture:** A Docker web container serves the SPA and proxies public API requests to a Docker backend. A separate callback proxy has only a loopback host port; the backend and web join the existing local Supabase network for server-side identity calls and browser OAuth redirects. SQLite/Pi state uses an isolated volume. The existing WSL AF3 receiver/tunnel and ports 18080/18084 stay in place.

**Tech Stack:** React/Vite, Nginx, Python 3.12/FastAPI, Node 22/Pi 0.87.1, Docker Compose, pytest, local fixed-version Supabase.

**Spec:** `docs/superpowers/specs/2026-10-02-agent-cloud-docker-deployment-design.md`

## Global Constraints

- Local host ports: web `127.0.0.1:18085`, backend diagnostics `127.0.0.1:18088`, AF3 proxy `127.0.0.1:18185`; never bind these test ports to `0.0.0.0`.
- Reuse the local external Docker network `pskit-supabase_default`, with Supabase gateway `api-gw:8000`; use a new SQLite/Pi volume, never the running WSL database.
- Pin Python, Node and Nginx base images to explicit tags plus verified digests. Do not use `latest`. Pi stays at the version locked in `new_backend/pi/package-lock.json`.
- Exclude `.env*`, `data/`, `node_modules/` and build caches from Docker build contexts. Put local test secrets in Git-ignored files with mode `0600`; never print them in checks.
- Preserve current uncommitted WSL backend/frontend work. Stage only files named by each task; no cloud or A6000 mutations in this plan.
- Follow the previously requested TDD sequence for code changes. Local integration runs the real Pi CLI against an isolated OpenAI-compatible model stub, using a test identity and mock AF3 worker; no real model tokens or second A6000 receiver.

## Review Focus

1. `SUPABASE_URL` points to Docker `api-gw` but Google redirects must use a browser-reachable URL: Task 1 tests both values and fallback compatibility.
2. The AF3 callback proxy accidentally binds a non-loopback local interface or routes to the wrong backend: Tasks 2 and 5 test rendered addresses and rejects.
3. Secrets leak into either image layer or frontend bundle: Tasks 3–5 inspect build contexts, images and generated assets.
4. Nginx buffers SSE, drops upload bodies or serves `/internal/`: Task 4 tests streaming, upload and route denial.
5. Docker restart loses an in-flight Run or AF3 journal state: Task 6 tests persistent volume and task recovery with the mock worker.

---

### Task 1: Separate Supabase internal and browser OAuth URLs

**Files:**
- Modify: `new_backend/app/config.py`
- Modify: `new_backend/app/api/auth.py`
- Modify: `new_backend/.env.example`
- Test: `new_backend/tests/test_live_adapters.py`
- Test: `new_backend/tests/test_live_capability_modes.py`

**Interfaces:** `Settings.supabase_url` remains the internal Python URL. Add `Settings.supabase_public_url: str = ""` from `SUPABASE_PUBLIC_URL`; `google_start()` uses `supabase_public_url or supabase_url`. Validate a configured public URL with the existing HTTP base URL validator.

- [ ] Add `test_google_start_uses_public_supabase_url` (redirect host is `agent.bioailab.net` while provider calls use `api-gw:8000`), `test_google_start_falls_back_to_internal_url`, and `test_invalid_supabase_public_url_rejected`.
- [ ] Run the new tests and confirm the expected failures.
- [ ] Add the field, environment mapping, validation and redirect change. Leave server-to-server Supabase calls on the internal URL.
- [ ] Run `python -m pytest -q tests/test_live_adapters.py tests/test_live_capability_modes.py` from `new_backend/`.
- [ ] Commit only the listed files with `feat: split Supabase public OAuth URL`.

### Task 2: Make the AF3 proxy usable inside Docker without broadening its routes

**Files:**
- Modify: `new_backend/scripts/af3_callback_proxy.py`
- Test: `new_backend/tests/test_af3_callback_proxy.py`

**Interfaces:** Keep `make_handler(key, worker_id, upstream_port)` callers valid; add optional `upstream_host="127.0.0.1"`. CLI adds `--listen-host` and `--upstream-host`, defaulting to `127.0.0.1`; Docker passes `0.0.0.0` inside the container and `backend` as upstream, while Compose controls the host binding.

- [ ] Add `test_proxy_uses_configured_upstream_host` (forwards only to `backend:8000`), plus wrong worker/key, non-AF3 path and oversized-body assertions; keep the default-host test.
- [ ] Run `python -m pytest -q tests/test_af3_callback_proxy.py` and confirm the new host test fails.
- [ ] Implement the two CLI options and host forwarding; keep the existing route and worker allowlists.
- [ ] Run `python -m pytest -q tests/test_af3_callback_proxy.py tests/test_af3_receiver_recovery.py`.
- [ ] Commit only the proxy and its tests with `feat: parameterize AF3 callback proxy network hosts`.

### Task 3: Build the Python/Pi backend image

**Files:**
- Create: `deploy/agent/backend.Dockerfile`
- Create: `new_backend/.dockerignore`
- Create: `deploy/agent/tests/test_backend_image.py`

**Interfaces:** Image runs `uvicorn app.main:app --host 0.0.0.0 --port 8000`, uses `/data` for SQLite and Pi sessions, and sets `RESEARCH_AGENT_INTERNAL_API_URL=http://127.0.0.1:8000`. Python 3.12 and Node 22 are pinned in the multi-stage Dockerfile; `npm ci` installs the locked Pi package. Container runs as a non-root UID able to write `/data`.

- [ ] Add `test_backend_image_runtime_and_storage` (Python >=3.12, Node 22, Pi executable/version, non-root UID, writable `/data`) and `test_backend_image_excludes_secrets` (`.env` absent from image and history).
- [ ] Run the checks before the Dockerfile exists and confirm a build/contract failure.
- [ ] Add `.dockerignore` and Dockerfile. Resolve and record the exact base-image digests before building.
- [ ] Run `docker build -f deploy/agent/backend.Dockerfile -t pskit-agent-backend:local new_backend` and the image contract checks.
- [ ] Commit only these three files with `build: package Python and Pi backend`.

### Task 4: Build the SPA web image and public route rules

**Files:**
- Create: `deploy/agent/web.Dockerfile`
- Create: `deploy/agent/web.conf`
- Create: `new_frontend/.dockerignore`
- Create: `deploy/agent/tests/test_web_routes.py`

**Interfaces:** `web` listens on container port 80; `/` serves the Vite build with SPA fallback, `/api/v1/` proxies to `backend:8000`, Google OAuth browser paths `/auth/v1/authorize` and `/auth/v1/callback` proxy to `api-gw:8000`, and `/internal/` plus all other `/auth/v1/` paths return 404. Build-time public values are `VITE_AUTH_MODE=supabase` and `VITE_API_BASE_URL=/api/v1`.

- [ ] Add `test_web_spa_and_api_routes` (deep link 200, API forwarding), `test_web_stream_and_upload` (SSE chunks arrive before completion, upload bytes match), and `test_web_rejects_private_routes` (only the two OAuth paths pass, `/internal/` 404).
- [ ] Run the route tests and confirm the expected build/config failure.
- [ ] Add pinned multi-stage Dockerfile, `.dockerignore` and Nginx config. Ensure no `.env.local` or backend secret enters the image.
- [ ] Run `docker build -f deploy/agent/web.Dockerfile -t pskit-agent-web:local new_frontend` and the route tests; run `nginx -t` after Compose provides the named upstreams in Task 5.
- [ ] Commit only the listed files with `build: package SPA with API gateway`.

### Task 5: Compose the isolated local stack

**Files:**
- Create: `deploy/agent/compose.yaml`
- Create: `deploy/agent/compose.local.yaml`
- Create: `deploy/agent/local.env.example`
- Create: `deploy/agent/.gitignore`
- Create: `deploy/agent/tests/mock_model_gateway.py`
- Create: `new_backend/tests/test_agent_compose_contract.py`

**Interfaces:** Compose services are `backend`, `web`, `af3-callback-proxy`; local override adds `model-gateway-mock` from a pinned Python image with only the test script mounted read-only. All join a private application network; backend and web also join an external network named by `SUPABASE_DOCKER_NETWORK` (locally `pskit-supabase_default`), while the AF3 proxy does not. Local override publishes ports 18085/18088/18185 to `127.0.0.1` only. Backend uses an ignored 0600 `env_file` for application credentials; proxy mounts a separate key-only 0600 file. Backend has an isolated named data volume. Local backend uses `RESEARCH_AGENT_RUNTIME=pi` and the mock gateway URL.

- [ ] Add `test_local_compose_is_private_and_persistent`: rendered Compose has expected services/networks, loopback-only ports, distinct backend/proxy secrets, persistent `/data`, and no unpinned image names. Add `test_model_stub_supports_suspend_and_resume`: normal response, `submit_af3` tool call and resumed response.
- [ ] Run the tests and confirm failure while files are absent.
- [ ] Add Compose files, model stub, safe examples and ignore rules. Populate local ignored files from existing local config without printing values; create a new test callback key rather than changing the live WSL key.
- [ ] Run `docker compose -f deploy/agent/compose.yaml -f deploy/agent/compose.local.yaml config --quiet` and the Compose contract tests. Run `nginx -t` after Task 6 starts the web service.
- [ ] Commit only templates/tests, never ignored credentials, with `build: add isolated local agent compose stack`.

### Task 6: Prove the local Docker flow and restart behavior

**Files:**
- Create: `deploy/agent/tests/smoke_local.py`
- Modify: `new_backend/README.md`
- Modify: `new_frontend/README.md`

**Interfaces:** `smoke_local.py` accepts a base URL and test credentials from environment or a 0600 file; it reports status/counts only. It creates a test user through local Supabase/Mailpit, logs in via Python, creates a project/session, sends a Pi Agent message to the model stub, reads SSE, uploads a small file, and triggers a low-cost AF3 tool call completed by the local mock worker.

- [ ] Add the smoke script and first run it against the not-yet-started local Compose stack to confirm connection failure.
- [ ] Start only the local stack with `docker compose ... up -d --build`; run `nginx -t` inside the web service and confirm existing 18080/18084 services remain active.
- [ ] Run the smoke to a suspended AF3 Run, restart `backend`/`af3-callback-proxy`, let the mock worker finish, and verify Pi auto-resumes with the test project, Run/job, quota and artifact state persisted; verify unauthenticated `/internal/` returns 404 via web.
- [ ] Run focused backend tests, `npm run typecheck`, `npm run lint`, `npm run build`, `git diff --check`, and inspect Compose logs for secret leakage. Record exact image digests and observed results in the READMEs.
- [ ] Commit only the smoke script and README changes with `docs: verify local Docker agent stack`.

## Handoff

Stop after Task 6. Show the local URL `http://127.0.0.1:18085`, verification results and remaining limits. Do not SSH to the cloud to mutate anything or switch the A6000 receiver until the user explicitly says to proceed with the separate cloud plan.
