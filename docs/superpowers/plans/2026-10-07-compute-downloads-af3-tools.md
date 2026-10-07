# Compute downloads and AF3 Tools implementation plan

> **For agentic workers:** Use superpowers:executing-plans in the current workspace, as already authorized by the user. Continue the approved API, SDK and UI test seams.

**Goal:** Deliver owned, durable CORAL downloads and expose real AF3 through the existing configured Tools workspace.

**Architecture:** Reuse `agent_artifact_blobs` in PostgreSQL, without a schema migration. The receiver reads bounded MCP chunks from an explicitly approved artifact tool, verifies size/hash, uploads through its fenced service grant, and retains its outbox until files and result commit. AF3 becomes a reviewed `job_mcp` Tool Product; a server-supplied job ID makes submissions recoverable in the existing native spool.

**Tech Stack:** FastAPI, PostgreSQL, MCP Python SDK, existing React Tool UI renderer.

**Spec:** Approved generic compute/admin plan (`2026-10-04-sandbox-compute-admin.md`), configured Tool Products plan (`2026-10-06-config-driven-mcp-tool-products.md`), and the user's request to enable file downloads and complete AF3 access.

## Constraints and review focus

- Keep provider paths and credentials private; no arbitrary URL downloads or filesystem paths from users.
- Fence file uploads by service, worker, attempt and token. Files become visible only after a matching terminal report; reject forged metadata and conflicting retries.
- Read chunks of at most 512 KiB; at most 64 MiB/file, 256 MiB/job and 128 files/job. Do not repeat inference after a lost file/result ACK.
- Recover AF3 only through an idempotent spool binding, and preserve the native inference container. Serialize generic and compatibility AF3 claims in the unified receiver.
- Expose real reported/estimated usage honestly. AF3 does not advertise confirmed cancellation or measured CPU time.
- Keep existing accounts, quotas, model keys, database schema and unrelated containers. Preserve private admin ingress.

## Execution

1. [x] HTTP/PostgreSQL tracer: fenced upload, terminal release, owned download/preview, conflicting hash and cross-user rejection. Implement `ComputeArtifacts` on existing blobs and wire catalog routes.
2. [x] SDK tracer: explicit `artifact_tool` binding, bounded chunk/hash verification, upload before terminal ACK, retry without repeated inference. Add provider read tools to CORAL and AF3.
3. [x] AF3 tracer: trusted submit job ID, safe spool recovery, pending qualification polling, estimated usage policy, private authenticated discovery and one serialized AF3 execution lane.
4. [x] Configured UI tracer: reusable JSON input field with invalid-input blocking; AF3 product/acceptance fixtures, input/result/artifact views, per-tool history.
5. [x] Independent code review, focused regression checks, batched commits. Validate Staging, publish the same verified backend/frontend artifacts and receiver source, qualify/publish AF3 and updated CORAL, verify real owned downloads and bounded native AF3 runs. Backfill existing owned CORAL outputs without inference.

## Completion evidence

- 221 related backend tests and 13 frontend component tests passed; frontend typecheck, lint and build passed. The unrelated full historical suite has existing migration/auth-fixture failures and is not claimed as green.
- CORAL's four reviewed real cases passed; AF3's ten-residue, one-seed, no-MSA/template native case passed. Both products are published at `/tools/coral` and `/tools/af3`.
- A public user run downloaded and verified one CORAL CSV and all 19 AF3 CIF/JSON outputs. Temporary test limits were restored while actual usage remained accounted.
- Two historical CORAL runs had two files restored and owner-authenticated downloads verified, with zero inference submissions for restoration.
- Receiver journals were empty after file/result acknowledgements; the original native AF3 container retained its 2026-10-02 start time.
- Provider restart retained the original module paths, configured Foldseek and an absolute workspace. CPU-only CORAL analysis accepts unknown GPU provenance while still requiring CPU/wall values.
- Final checks caught a local Turnstile test site key in the first frontend artifact. The original login index was restored; a corrected dist was built from production public settings, checked on Staging and published identically. Final asset hashes and site-key continuity passed.

Release facts and evidence paths: [2026-10-07 downloads and AF3 release](../../releases/2026-10-07-compute-downloads-af3.md).
