# Workspace file tools and rich Markdown artifacts

Date: 2026-10-10 (Asia/Shanghai)

## Release

- Source commits, both pushed on `codex/new-stack-baseline`: `52e934c` (`fix(agent): expose file tools and rich artifact previews`) and `e1a8caf` (`build(backend): pin runtime dependencies`).
- The Pi extension registers `read_file`, `write_file`, `edit_file`, `list_files`, `find_files`, and `search_files`; the system prompt tells Pi to invoke these tools and verify successful writes instead of emitting pseudo tags such as `<write_file>`.
- Markdown artifacts in chat and the Artifacts panel use the shared safe Markdown renderer, including syntax-highlighted code blocks. Artifact downloads keep their Blob URL alive long enough for the browser to start the download.
- Backend image tag: `pskit-agent-backend:20261010-e1a8caf`.
- Local build image ID: `sha256:4fad8b9d1fb458e7d89dc4d7ca49ffcc15f27af74257ecdc7394a359b239cd00`.
- Aliyun image ID after Docker archive import: `sha256:781d6dbc3b12f657d10e5038db26ca78daa98fc8296824aea025361782e15757`. Docker's saved legacy archive represents the image configuration differently, so import assigned a different image ID. The local and imported image have the same 20 RootFS layer digests (combined SHA256 `28a1c9862dd569f136524ad9098093c0ebc8c03a28eb780cccae4dc74e843ad2`), and the imported image's 45 Python package versions match the prior production container. Staging and production both run the Aliyun image ID above.
- Frontend `dist` SHA256: `23321b7cb49235c268f5ff0894957fe43f9dfe3575539629fd02020958d74911` (401 files). `index.html` SHA256: `45f05e600e65cd9559c11620d4597a7c7648ebee422cba3f5596639848963ae3`.
- The frontend was built with the current production Turnstile public site key. The deployed JavaScript contains that configured key; the key value is intentionally not recorded here.

## Verification

- Staging used its existing isolated database, synthetic account, model stub, and gVisor workspace. API smoke passed login, file upload, SSE, quota enforcement, and simulated AF3; the production database snapshot remained unchanged.
- OpenSandbox workspace smoke reported `live-ready`, including file/artifact persistence, user and session isolation, cancellation, output truncation, and the pinned `runsc` runtime. Private Staging Nginx login returned HTTP 200; Staging `index.html` and the chat/Markdown JavaScript assets matched the release byte for byte.
- Production was checked for active Runs, Jobs, and workspace attempts before cutover; all counts were zero. Backend is healthy on the new image with restart count 0. The AF3 callback proxy was left on its existing image.
- Production Nginx returned HTTP 200 for `/login`; unauthenticated `/api/v1/usage` returned 401 and `/internal/` returned 404. The deployed frontend assets and dist hash match the release. No database migration, user-data migration, real model invocation, or GPU task was run.
- The Staging model stub does not call Pi's `write_file` tool. Therefore the build and sandbox/artifact storage path are verified, but a real provider's decision to invoke the function must still be confirmed with a new Markdown-file request in the production chat. The screenshot's earlier `<write_file>` text did not create a file and cannot retroactively appear in Artifacts.

## Rollback

The production backend pin backup is `/home/ecs-user/pskit-agent-releases/20261010-e1a8caf/cloud.env.production.previous.20261009T164155Z` (mode 0600). The previous frontend index is `/home/ecs-user/pskit-agent-releases/20261010-e1a8caf/index.production.previous` (mode 0600); prior hashed assets remain in the web root.

To roll back the backend pin and recreate only the backend service, from Aliyun:

```bash
AGENT=/home/ecs-user/pskit-agent-cloud-20261002/deploy/agent
RELEASE=/home/ecs-user/pskit-agent-releases/20261010-e1a8caf
cp "$RELEASE/cloud.env.production.previous.20261009T164155Z" "$AGENT/.cloud.env.rollback"
chmod 600 "$AGENT/.cloud.env.rollback"
mv "$AGENT/.cloud.env.rollback" "$AGENT/cloud.env"
docker compose --env-file "$AGENT/cloud.env" \
  -f "$AGENT/compose.yaml" -f "$AGENT/compose.cloud.yaml" \
  -f "$AGENT/compose.postgres.yaml" -f "$AGENT/compose.opensandbox.yaml" \
  -f "$AGENT/compose.opensandbox.production.yaml" -p pskit-agent-cloud \
  up -d --no-deps --wait backend
```

To restore the previous frontend, copy `index.production.previous` to a temporary file in `/var/www/agent.bioailab.net`, set mode 0644, then atomically rename it to `/var/www/agent.bioailab.net/index.html`. Do not delete the newer assets or restore database volumes.
