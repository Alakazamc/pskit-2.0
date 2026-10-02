# Research Agent Cloud Docker Rollout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After the local Docker stack passes and the user asks to proceed, run the new Agent on Aliyun at `https://agent.bioailab.net` and reconnect A6000 AF3 through WireGuard.

**Architecture:** Deploy the exact locally validated web/backend images with Docker Compose next to a fresh, fixed-version self-hosted Supabase. Host Nginx terminates HTTPS for the browser and separately accepts AF3 callbacks only on `10.9.8.1:18184`, forwarding to a loopback-only container port. A6000 pulls jobs from that private entrance; old PSKit stays untouched.

**Tech Stack:** Docker Compose, pinned Supabase self-hosted v0.8.2 snapshot, Nginx, WireGuard, Python/Pi, React SPA, SMTP.

**Spec:** `docs/superpowers/specs/2026-10-02-agent-cloud-docker-deployment-design.md`

## Global Constraints

- **Execution gate:** This plan is preparatory. Do not mutate Aliyun or A6000 until the local plan is verified and the user explicitly asks to proceed with cloud rollout.
- `aliyun` WireGuard is `10.9.8.1`; A6000 is `10.9.8.2`; ignore `10.9.8.3`. Old `pskit.bioailab.net`, its container and Nginx site must remain operational.
- Cloud app ports: `127.0.0.1:18085` web, `127.0.0.1:18088` backend diagnostics, `127.0.0.1:18185` AF3 proxy. Host Nginx alone listens on private `10.9.8.1:18184`; no Docker-published port binds `wg0` or the public interface.
- `agent.bioailab.net` needs DNS, HTTPS cert and host Nginx changes by an operator with sudo. Current `ecs-user` SSH login has no noninteractive sudo. Real SMTP credentials are provided later and never committed.
- Fresh cloud Supabase keys, Postgres, SQLite and Pi sessions; no migration of WSL test users/data. Fixed image tags/digests; no `latest` or dev Mailpit in cloud.
- Do not expose `/internal/`, Supabase Studio/REST/admin or the AF3 proxy on the public website. Docker-published ports can bypass UFW; rely on loopback publication plus host Nginx source filtering and UFW for the private entrance.
- A6000 compute container and spool must not be interrupted. Switch only the receiver after both its journal and the old backend queue are empty.
- Until cancellation can stop a running AF3 process and larger artifacts have a storage path, launch with `RESEARCH_AGENT_MEMBER_DAILY_GPU_MINUTES=0` and `RESEARCH_AGENT_GUEST_DAILY_GPU_MINUTES=0`; grant only the cloud test account a 60-minute daily GPU override via the existing admin API. Widen access in a separate reviewed change.

## Review Focus

1. DNS or TLS points to the wrong host: Task 3 checks certificate SAN, Host header and the new domain before any user login.
2. Docker bridge publication bypasses UFW and exposes secrets: Task 2 checks every published host IP and Task 3 probes the public address.
3. Supabase generates links to its internal `api-gw` hostname: Tasks 1 and 3 verify external Auth URLs and email OTP delivery.
4. A6000 switches while an old claim remains: Task 4 checks old queue/journal emptiness before changing receiver configuration.
5. A callback retries after a connection failure or duplicate completion: Task 4 checks task/lease identity, artifact count, GPU reconciliation and spool cleanup.

---

### Task 1: Prepare the independent cloud Supabase stack

**Files:**
- Create: `infra/supabase/compose.cloud.yaml`
- Create: `infra/supabase/cloud.env.example`
- Create: `deploy/agent/tests/test_cloud_supabase_config.py`
- Modify: `infra/supabase/PSKIT.md`

**Interfaces:** Use the checked-in `self-hosted/v0.8.2` Compose snapshot with a cloud overlay named `pskit-agent-supabase`. Publish the Supabase gateway and optional database administration only on `127.0.0.1`; connect application containers over its external Compose network. Retain the fixed-version read-only OTP `templates-server` and JWT/JWKS wiring from `compose.pskit.yaml`, but replace Mailpit with real SMTP. Configure fresh keys and `https://agent.bioailab.net` external URLs. Cloud project volumes and container names are separate from the old `pskit` service.

- [ ] Add `test_cloud_supabase_is_private_and_uses_real_smtp`: rendered Compose has fixed image tags, loopback-only host publications, database/storage mounts under the new cloud project directory, OTP templates, no Mailpit service and required SMTP variables.
- [ ] Run the test against absent cloud files and confirm the expected failure.
- [ ] Write the overlay/example/docs, then run `docker compose -f infra/supabase/docker-compose.yml -f infra/supabase/compose.cloud.yaml config --quiet` with dummy local values.
- [ ] Commit only these files with `build: prepare cloud Supabase stack`.

### Task 2: Prepare cloud app, host Nginx and operator handoff

**Files:**
- Create: `deploy/agent/compose.cloud.yaml`
- Create: `deploy/agent/cloud.env.example`
- Create: `deploy/agent/host-nginx-agent.conf`
- Create: `deploy/agent/host-nginx-af3.conf`
- Create: `deploy/agent/OPERATIONS.md`
- Create: `deploy/agent/tests/test_cloud_routes.py`

**Interfaces:** The cloud override reuses the local tested images and data volume contract, swaps the Supabase external network, and keeps host ports on loopback. Host Nginx proxies `agent.bioailab.net` HTTPS to `127.0.0.1:18085` and private `10.9.8.1:18184` from `10.9.8.2` to `127.0.0.1:18185`. `OPERATIONS.md` contains exact DNS, certificate, SMTP, UFW and `sudo nginx -t` commands with verification and rollback steps.

- [ ] Add `test_cloud_compose_has_no_public_container_ports` and `test_private_nginx_allows_only_a6000`: rendered ports use loopback, host Nginx listens on `10.9.8.1:18184`, allows `10.9.8.2`, denies others, proxies to `127.0.0.1:18185`, and never names the old site as an output file.
- [ ] Run the tests to see the expected missing-file failures.
- [ ] Write the override, host templates and handoff. Explicitly document the existing `.1`↔`.2` TCP timeout and require `sudo wg show`/`sudo ufw status verbose` diagnosis before an allow rule is applied.
- [ ] Run the Compose render tests and `git diff --check`. Commit only these files with `docs: prepare isolated cloud agent rollout`.

### Task 3: Deploy on Aliyun after the user releases the cloud gate

**Files:**
- Use: images produced by `2026-10-02-agent-local-docker-validation.md`
- Use: `infra/supabase/compose.cloud.yaml`, `deploy/agent/compose.cloud.yaml`, `deploy/agent/OPERATIONS.md`
- Update: ignored cloud 0600 environment and key files only

**Interfaces:** Export images with immutable SHA-based tags and verify digests after transfer; do not rebuild with different source on Aliyun. Create fresh cloud Supabase keys. SMTP, DNS, TLS, Nginx and UFW changes require the user's/operator's sudo access and values.

- [ ] Confirm the user's explicit instruction to proceed, plus the local verification report, cloud disk/RAM, and old service health.
- [ ] Transfer only build artifacts and reviewed Compose/templates; create an isolated cloud directory. Generate new Supabase/AF3 secrets on cloud, configure SMTP without printing values, then start Supabase and app containers.
- [ ] Verify Docker health, `127.0.0.1:18085` page/API, `127.0.0.1:18088/health/ready`, `127.0.0.1:18185` AF3 allowlist, new user signup/email OTP/login, SSE and file upload before enabling the public site. Check the default GPU limit is zero and only the test account has an override.
- [ ] After the operator installs DNS/certificate/Nginx rules, verify `https://agent.bioailab.net` from outside, cookie `Secure`, OAuth URL shape, public `/internal/` 404, and old `pskit.bioailab.net` unchanged. Do not claim Google OAuth until a real provider flow passes.
- [ ] Save image digests, volume backup paths and health results in `OPERATIONS.md`; keep the cloud stack running only after checks pass.

### Task 4: Restore private connectivity and safely switch AF3

**Files:**
- Use: `new_backend/scripts/af3_receiver.py`, `new_backend/scripts/af3_callback_proxy.py`
- Update: ignored cloud/A6000 callback key and receiver deployment settings only
- Record: `deploy/agent/OPERATIONS.md`

**Interfaces:** Cloud host Nginx exposes private `10.9.8.1:18184` only to `10.9.8.2`; A6000 receiver uses worker ID `a6000-af3-cloud-1` and new cloud key. Compute container and spool stay mounted/running. The receiver never sends a cloud result to the old WSL backend.

- [ ] Ask the operator for `sudo wg show` and `sudo ufw status verbose` on both hosts, then fix only the route/firewall rule needed for `10.9.8.2 → 10.9.8.1:18184/tcp`. Confirm with a keyed read-only `GET /internal/compute/af3/jobs/owned`; do not expose public 18184.
- [ ] Confirm old WSL AF3 queue and A6000 journal have zero unfinished jobs; back up the receiver settings. Stop/recreate only the receiver with the cloud URL/key/worker ID, preserving compute and spool.
- [ ] Submit one small real AF3 task through a new cloud test user. Check claim, progress, `simulation=false`, artifact download, GPU charge, backend ACK, spool cleanup and Pi background wakeup. Run duplicate callback/restart recovery checks without re-running the GPU calculation.
- [ ] If the test fails, keep the cloud claim and A6000 spool for reconciliation; roll the receiver back only after unfinished cloud jobs reach a safe terminal state. Record the results and remaining AF3 cancellation/20 MiB artifact limits.

## Handoff

Tasks 1–2 prepare reviewable files locally. Tasks 3–4 are held until the user asks to deploy after local testing. The operator must receive a precise DNS/Nginx/SMTP/WireGuard command list; **no 10.9.8.3 port forwarding is required** by this design.
