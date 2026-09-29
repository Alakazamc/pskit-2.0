# Scripts

The supported delivery path is Docker Compose; see `DOCKER_QUICKSTART.md`.
Packaging scripts export a pinned application/Qdrant image bundle and a
sanitized single-file offline delivery archive. `docker_smoke.sh` verifies
health, frontend assets, registration, session cookies, protected task listing,
logout, and login.

`docker_smoke.sh` requires a POSIX shell, `curl`, standard text utilities and
Python 3. On a fresh production database set `PSKIT_SMOKE_BOOTSTRAP_TOKEN`
to the configured server bootstrap token; it is only sent when the server
reports that first-admin bootstrap is required. Otherwise create the initial
administrator first, or supply an existing `PSKIT_SMOKE_USERNAME` and
`PSKIT_SMOKE_PASSWORD`. Generated smoke accounts are retained. The smoke test
covers HTTP/authentication/UI delivery, not real scientific model inference.

Native development helpers can start the backend, worker, frontend build, RAG
indexer, and smoke test. Copy `.env.example` to `.env`, keep all keys private,
then use `pskit2_ctl.sh` for a background development deployment. Runtime PID
files and logs default to `/tmp/pskit2` and can be moved with `PSKIT_RUN_DIR`.

Never put credentials, user databases, artifacts, model weights, or private
infrastructure addresses in scripts or a delivery bundle.
