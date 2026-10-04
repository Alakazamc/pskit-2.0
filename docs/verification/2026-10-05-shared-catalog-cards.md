# Shared catalog cards and per-tool history

Date: 2026-10-05

## Delivered

- Recorded compact catalog and detail-panel rules in `new_frontend/AGENTS.md`.
- Added shared `CatalogCard`, `CatalogSearch`, `DetailPanel` and `CatalogLibrary` primitives; Tools, Skills/resources, project index and artifacts reuse the card. Removed the replaced large-card styles.
- Tools retain their directory behind a centered detail panel. Parameters and history are separate tabs; entered parameters survive tab changes. The pencil returns to parameters and focuses an editable field after the tab has rendered.
- Removed the aggregate Tools history entry. Existing `/tools/runs` links redirect to the tool directory. PDB result history links target that tool's history; project results retain their existing aggregation and assignment behavior.
- Added optional exact-name filtering to `GET /api/v1/tool-runs?tool=...`, scoped to the authenticated owner in memory, SQLite and PostgreSQL. Query caches include both user and tool. Regenerated OpenAPI and TypeScript contracts.
- Skills/resource catalog metadata is currently read-only. The shared panel exposes an edit action only when the caller has an actual edit operation. Tool editing means invocation parameters; Skill package file editing and user-owned catalog write APIs remain separate work.

## Evidence

Tests were written against the public UI/HTTP boundary. Before implementation, tool-panel and Skill/artifact-card scenarios failed; the HTTP history regression returned records for another tool. The completed scenarios pass.

| Check | Result |
| --- | --- |
| Frontend complete Vitest suite | 32 files, 209 tests passed |
| Final focused catalog tests | 3 passed |
| Backend offline suite | 419 passed; 192 environment-dependent tests skipped |
| PostgreSQL history after reopen | 1 passed in a unique test schema, automatically removed |
| TypeScript / ESLint / production build | Passed; existing large Molstar/Markdown chunk warnings remain |
| Ruff on changed Python files | Passed |
| Chromium, desktop/mobile × dark/light | 4 scenarios passed; zero page errors |

Browser inspection measured the representative card at 100 × 81 px in all four scenarios. Desktop panels measured 720 px wide; mobile panels measured 370 px in a 390 px viewport. Search fields had zero border, outline and shadow while focused, with a theme background on their container. Card keyboard focus remained visible. Checks also covered Escape/focus restoration, parameter retention, pencil focus, exact per-tool history requests, structure-viewer panel width, and card reuse across the other catalogs.

All browser APIs used synthetic responses. No paid model or GPU calls, production database writes or cloud deployment were performed.

## Reproduce

Run the scripts in each package's `package.json` for typecheck, lint, tests and build. Export contracts with `.venv/bin/python -m scripts.export_openapi` from `new_backend`, then run frontend `generate:api`.

For backend tests, add the repository root to `PYTHONPATH`. For `tests/postgres/test_tool_history.py`, set `TEST_POSTGRES_DSN` to the isolated test PostgreSQL described in `deploy/agent/tests/compose.pg17.test.yaml`.

Start a temporary frontend with demo auth and run `new_frontend/tests/browser/catalog_tools.py --executable <installed Chromium path>`. The browser script intercepts every `/api/v1/` request. Inspection JSON and screenshots are written under `/tmp/pskit-catalog-tools` by default.
