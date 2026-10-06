# Public Registration Abuse Protection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Open email registration to the public while protecting authentication, SMTP, Python, and PostgreSQL with shared rate limits, server-verified CAPTCHA, trusted client IPs, and clear client cooldowns.

**Architecture:** Host Nginx performs coarse route-specific shedding, Python resolves the trusted client address and applies an atomic PostgreSQL `AuthAbuseGuard`, then verifies Cloudflare Turnstile before calling Supabase GoTrue. Supabase keeps account/session/mail semantics and explicit inner limits; React sends one-time CAPTCHA tokens and renders `Retry-After` countdowns. No Redis or paid Alibaba Cloud DDoS/WAF product is introduced.

**Tech Stack:** FastAPI, Pydantic, PostgreSQL, psycopg, httpx, Supabase GoTrue `v2.196.0`, Cloudflare Turnstile, React, TypeScript, Vitest, Nginx, Docker Compose

**Spec:** `docs/superpowers/specs/2026-10-06-public-registration-abuse-protection-design.md`

## Global Constraints

- Email registration is public to all valid domains; no school-domain allowlist or invite code.
- Browsers call only Python `/api/v1/auth/*`; Turnstile secret and Supabase service credentials remain server-side.
- Do not configure or purchase Alibaba Cloud DDoS/WAF in this implementation; local controls must not be described as bandwidth-DDoS protection.
- Production rate-limit truth is the existing PostgreSQL cluster; do not add Redis, SQLite fallback, or process-local correctness state.
- Store only namespaced HMAC-SHA256 identifiers, action, rule, window, count, claim state, and expiry; never persist raw email, raw IP, password, OTP, JWT, Cookie, or CAPTCHA token.
- Every bucket applicable to one request is checked and claimed in one PostgreSQL transaction with deterministic locking; any failure rolls back all bucket changes.
- Only a provider failure proven to occur before request transmission may refund a claim. Read/write timeout, response loss, provider 4xx/429, and any ambiguous outcome retain the claim and are never automatically retried.
- Initial email limits are 1/60 seconds, 5/hour, 10/UTC day per email and 20/10 minutes, 100/UTC day per IP.
- Initial OTP limit is 10 attempts per email+IP in 10 minutes followed by a 15-minute lock; login limits are 10/15 minutes per account and 30/5 minutes per IP.
- Nginx auth limits start in `limit_req_dry_run on`; enforcement follows 24–48 hours of real traffic review.
- Preserve Google OAuth, refresh cookies, anonymous-auth semantics, SSE, uploads, Agent Runs, private admin routing, loopback-only backend/Supabase ports, and the old site.
- Staging and production keep separate data, mail, HMAC, and Turnstile settings. Automated tests never call real SMTP or Turnstile.
- Schema migration runs before the new web image; the web process does not auto-migrate.
- This plan changes repository code and deploy artifacts only. Cloud deployment requires a separate explicit release action.

## Review Focus

- A public client forges `X-PSKit-Client-IP` or `X-Forwarded-For`: Python must ignore it unless the socket peer is in the exact trusted proxy CIDRs.
- Two Python instances race at a window boundary: PostgreSQL must admit only the configured number and leave no partially charged buckets.
- Supabase receives a request but its response is lost: the claim must remain consumed and the adapter must not retry.
- Many legitimate users share one IPv4 NAT or use IPv6: normal flows stay usable and non-auth routes never enter auth zones.
- Web Crypto, `sessionStorage`, Turnstile, or storage access fails: the UI remains understandable, secrets are never persisted, and protected sends fail closed without losing the typed email.

---

### Task 1: PostgreSQL Atomic Auth Abuse Guard

**Files:**
- Create: `new_backend/app/domain/auth_abuse.py`
- Create: `new_backend/app/db/postgres_migrations/008_auth_abuse.sql`
- Modify: `new_backend/app/db/postgres_migrations/__init__.py`
- Modify: `new_backend/tests/postgres/test_schema.py`
- Create: `new_backend/tests/postgres/test_auth_abuse.py`
- Modify: `deploy/agent/tests/test_shared_litellm_postgres.py`

**Interfaces:**
- Consumes: `PostgresDatabase.transaction()` and a shared secret of at least 32 characters.
- Produces: `AuthClaim`, `AuthAbuseLimited`, and `AuthAbuseGuard.claim_email_send()`, `claim_login()`, `claim_verification()`, `settle()`, and `cleanup_expired()`.

- [x] **Step 1: Write failing schema and guard tests**

Cover schema version 8 and least-privilege access; namespaced HMAC keys; atomic email/IP multi-window claims; 60-second cooldown across a fixed-window boundary; hourly, UTC-day, OTP lock, and login windows; longest positive `Retry-After`; two independent guard instances racing; no partial deduction; idempotent settlement; success/not-sent/unknown outcomes; and cleanup independent of correctness.

- [x] **Step 2: Run the focused tests and confirm RED**

Run: `cd new_backend && TEST_POSTGRES_DSN=postgresql://postgres:pskit-test-only@127.0.0.1:15433/postgres PYTHONPATH=.:.. .venv/bin/python -m pytest tests/postgres/test_auth_abuse.py tests/postgres/test_schema.py ../deploy/agent/tests/test_shared_litellm_postgres.py -q`

Expected: FAIL because schema version 8 and `AuthAbuseGuard` do not exist.

- [x] **Step 3: Add migration 008 and implement the guard**

Create bucket, cooldown/lock, claim, and claim-item tables with expiry indexes and no raw PII columns. Use deterministic PostgreSQL advisory locks plus a single transaction for all rules; use an injectable UTC clock. Implement these signatures:

```python
AuthAction = Literal[
    "signup_send", "recovery_send", "guest_upgrade_send",
    "password_login", "otp_verify", "guest_upgrade_verify",
]

@dataclass(frozen=True)
class AuthClaim:
    token: str
    action: AuthAction

class AuthAbuseLimited(Exception):
    retry_after: int
    rule: str

class AuthAbuseGuard:
    def consume_send_ip(self, action: AuthAction, client_ip: str) -> None: ...
    def claim_email_send(self, action: AuthAction, email: str) -> AuthClaim: ...
    def claim_login(self, email: str, client_ip: str) -> AuthClaim: ...
    def claim_verification(self, action: AuthAction, email: str, client_ip: str) -> AuthClaim: ...
    def settle(self, claim: AuthClaim, outcome: Literal["success", "rejected", "not_sent", "unknown"]) -> None: ...
    def cleanup_expired(self, limit: int = 1000) -> int: ...
```

Email-send IP items never refund. Email-send claims refund only `not_sent`. Login/verification `success` and `not_sent` release account/email items while retaining IP attempts. `rejected` and `unknown` retain all items.

- [x] **Step 4: Run focused tests and confirm GREEN**

Run the Step 2 command.

Expected: PASS, including concurrent two-instance and permissions tests.

- [x] **Step 5: Commit the database guard slice**

```bash
git add new_backend/app/domain/auth_abuse.py new_backend/app/db/postgres_migrations new_backend/tests/postgres deploy/agent/tests/test_shared_litellm_postgres.py
git commit -m "feat: add atomic auth abuse guard"
```

### Task 2: Trusted Client IP and Provider Outcome Semantics

**Files:**
- Create: `new_backend/app/services/client_ip.py`
- Modify: `new_backend/app/adapters/live/supabase_auth.py`
- Modify: `new_backend/app/ports/providers.py`
- Modify: `new_backend/app/config.py`
- Create: `new_backend/tests/test_client_ip.py`
- Modify: `new_backend/tests/test_live_adapters.py`
- Create: `new_backend/tests/test_config.py`

**Interfaces:**
- Consumes: exact `trusted_proxy_cidrs` and internal header name `X-PSKit-Client-IP`.
- Produces: `TrustedClientIpResolver.resolve(peer, asserted) -> str` and provider transport failures classified as `not_sent` or `unknown` without retries.

- [x] **Step 1: Write failing trusted-IP and provider tests**

Cover untrusted spoofed custom header and XFF, trusted loopback proxy, invalid asserted address fallback, IPv4-mapped IPv6 normalization, empty peer, internal header overwrite on signup/recover/login/verify/guest-upgrade, one request on 429, connect failure `not_sent`, and read/write/response failures `unknown`.

- [x] **Step 2: Run the focused tests and confirm RED**

Run: `cd new_backend && PYTHONPATH=.:.. .venv/bin/python -m pytest tests/test_client_ip.py tests/test_live_adapters.py tests/test_config.py -q`

Expected: FAIL because the resolver, settings, header propagation, and outcome classification do not exist.

- [x] **Step 3: Implement exact proxy trust and provider classification**

Implement:

```python
class TrustedClientIpResolver:
    def __init__(self, trusted_proxy_cidrs: tuple[str, ...], header: str = "X-PSKit-Client-IP"): ...
    def resolve(self, peer: str | None, asserted: str | None) -> str: ...

class IdentityTransportUnavailable(ProviderUnavailable):
    delivery: Literal["not_sent", "unknown"]
```

Only `ConnectError`, `ConnectTimeout`, and `PoolTimeout` before transmission may be `not_sent`; everything ambiguous is `unknown`. Add keyword-only `client_ip` to affected identity adapter methods and construct the internal header exclusively from the resolver result. Add strict settings for abuse mode, HMAC secret, trusted CIDRs, CAPTCHA requirements, Turnstile hostnames, and rate policy; live protection rejects invalid or missing required values.

- [x] **Step 4: Run focused tests and confirm GREEN**

Run the Step 2 command.

Expected: PASS with no adapter retry and no caller-controlled internal IP.

- [x] **Step 5: Commit the trusted network slice**

```bash
git add new_backend/app/services/client_ip.py new_backend/app/adapters/live/supabase_auth.py new_backend/app/ports/providers.py new_backend/app/config.py new_backend/tests/test_client_ip.py new_backend/tests/test_live_adapters.py new_backend/tests/test_config.py
git commit -m "feat: trust and propagate verified auth client IPs"
```

### Task 3: Turnstile and Protected Authentication Orchestration

**Files:**
- Create: `new_backend/app/ports/captcha.py`
- Create: `new_backend/app/adapters/live/turnstile.py`
- Create: `new_backend/app/services/auth_protection.py`
- Modify: `new_backend/app/contracts/models.py`
- Modify: `new_backend/app/api/auth.py`
- Modify: `new_backend/app/api/guest_auth.py`
- Modify: `new_backend/app/main.py`
- Modify: `new_backend/app/services/observability.py`
- Create: `new_backend/tests/test_turnstile.py`
- Create: `new_backend/tests/test_auth_protection.py`
- Modify: `new_backend/tests/test_identity_policy.py`
- Modify: `new_backend/tests/test_guest_email_upgrade.py`
- Modify: `new_backend/tests/postgres/test_app_wiring.py`
- Modify: `new_backend/tests/test_observability.py`

**Interfaces:**
- Consumes: Task 1 claims, Task 2 client IP and transport outcome.
- Produces: protected signup, recovery, login, OTP, and guest-email-upgrade endpoints with stable CAPTCHA/rate-limit/unavailable errors and PII-free security metrics.

- [x] **Step 1: Write failing verifier, orchestration, route, wiring, and metric tests**

Cover Turnstile payload and no retries; missing/invalid/expired/duplicate token; action and hostname mismatch; network timeout fail-closed; IP accounting before CAPTCHA; email claim only after valid CAPTCHA; concurrent send admits one upstream request; positive integer `Retry-After`; identical recovery response for known and unknown email; provider outcome settlement; login/OTP success release; guest upgrade parity; startup validation; shared PostgreSQL state; and fixed-dimension metrics without PII.

- [x] **Step 2: Run the focused tests and confirm RED**

Run: `cd new_backend && PYTHONPATH=.:.. .venv/bin/python -m pytest tests/test_turnstile.py tests/test_auth_protection.py tests/test_identity_policy.py tests/test_guest_email_upgrade.py tests/postgres/test_app_wiring.py tests/test_observability.py -q`

Expected: FAIL because the verifier and orchestration service do not exist and DTOs lack `captcha_token`.

- [x] **Step 3: Implement Turnstile verification and the one protected auth entry point**

Implement:

```python
class CaptchaVerifier(Protocol):
    async def verify(self, token: str, *, remote_ip: str, action: str) -> None: ...

class AuthProtection:
    async def authorize_email_send(self, action: AuthAction, email: str, captcha_token: str | None, *, peer_ip: str | None, asserted_ip: str | None) -> AuthClaim: ...
    def authorize_login(self, email: str, *, peer_ip: str | None, asserted_ip: str | None) -> AuthClaim: ...
    def authorize_verification(self, action: AuthAction, email: str, *, peer_ip: str | None, asserted_ip: str | None) -> AuthClaim: ...
    def settle(self, claim: AuthClaim, outcome: Literal["success", "rejected", "not_sent", "unknown"]) -> None: ...
```

Use fixed route-to-action mappings `signup`, `recovery`, and `guest_upgrade_email`; never accept expected action from the browser. Split the guest upgrade start and verify DTOs so CAPTCHA is required only for sending. Return `CAPTCHA_REQUIRED`/`CAPTCHA_INVALID` as 422, `AUTH_RATE_LIMITED` with integer `Retry-After` as 429, and `AUTH_CAPTCHA_UNAVAILABLE`/`IDENTITY_UNAVAILABLE` as 503. Add injected verifier/guard wiring for tests and a bounded expired-row cleanup task. Leave the existing anonymous GoTrue CAPTCHA path unchanged.

- [x] **Step 4: Run focused tests and confirm GREEN**

Run the Step 2 command.

Expected: PASS, including the timeout-retains-claim and recovery-enumeration tests.

- [x] **Step 5: Commit the protected auth slice**

```bash
git add new_backend/app/ports/captcha.py new_backend/app/adapters/live/turnstile.py new_backend/app/services/auth_protection.py new_backend/app/contracts/models.py new_backend/app/api/auth.py new_backend/app/api/guest_auth.py new_backend/app/main.py new_backend/app/services/observability.py new_backend/tests
git commit -m "feat: protect public auth with Turnstile and shared limits"
```

### Task 4: React CAPTCHA and Server-Governed Cooldowns

**Files:**
- Modify: `new_frontend/src/api/http.ts`
- Modify: `new_frontend/src/api/http.test.ts`
- Modify: `new_frontend/src/api/types.ts`
- Create: `new_frontend/src/features/auth/AuthCaptcha.tsx`
- Create: `new_frontend/src/features/auth/AuthCaptcha.test.tsx`
- Remove: `new_frontend/src/features/auth/GuestCaptcha.tsx`
- Create: `new_frontend/src/features/auth/authCooldown.ts`
- Create: `new_frontend/src/features/auth/authCooldown.test.ts`
- Modify: `new_frontend/src/features/auth/LoginPage.tsx`
- Modify: `new_frontend/src/features/auth/LoginPage.live.test.tsx`
- Modify: `new_frontend/src/features/auth/GuestUpgrade.tsx`
- Modify: `new_frontend/src/features/auth/GuestUpgrade.test.tsx`
- Modify: `new_frontend/src/i18n/errors.ts`
- Modify: `new_frontend/src/i18n/errors.test.ts`
- Modify: `new_frontend/src/i18n/translations.ts`
- Modify: `new_frontend/src/styles/login.css`
- Modify: `new_frontend/src/styles/mono-pages.css`

**Interfaces:**
- Consumes: backend `captcha_token`, stable error codes, and `Retry-After` from Task 3.
- Produces: reusable `AuthCaptcha`, `ApiError.retryAfterSeconds`, and per-action/email browser cooldown state containing only an expiry timestamp.

- [x] **Step 1: Write failing API, CAPTCHA, cooldown, page, and i18n tests**

Cover numeric `Retry-After`; invalid/date/zero values ignored; no auth retry; action passed to Turnstile; expiry/error/reset/action-change handling; storage key SHA-256 over trimmed lowercase email; no raw email/credential/token storage; storage/Web Crypto failure fallback; signup/recovery/guest upgrade token submission; token reset after every request; 429 server countdown; success minimum 60 seconds; refresh recovery; action/email isolation; stable bilingual error copy; and unchanged login/OAuth/OTP flows.

- [x] **Step 2: Run focused tests and confirm RED**

Run: `cd new_frontend && npm test -- src/api/http.test.ts src/features/auth/AuthCaptcha.test.tsx src/features/auth/authCooldown.test.ts src/features/auth/LoginPage.live.test.tsx src/features/auth/GuestUpgrade.test.tsx src/i18n/errors.test.ts`

Expected: FAIL because the shared component, cooldown module, and header parsing do not exist.

- [x] **Step 3: Implement the reusable component and cooldown UX**

Implement:

```ts
class ApiError extends Error {
  readonly code?: string
  readonly retryAfterSeconds?: number
  constructor(readonly status: number, detail: unknown, retryAfterHeader?: string | null)
}

type AuthCaptchaProps = {
  siteKey: string
  action: "signup" | "recovery" | "guest_upgrade_email" | "guest_anonymous"
  resetSignal: number
  onToken(token: string | null): void
  onUnavailable?(): void
}

type AuthMailAction = "signup" | "recovery" | "guest-upgrade"
```

The cooldown key is `pskit:auth-cooldown:v1:{action}:{sha256(normalizedEmail)}` and its only value is `{ "expiresAt": number }`. CAPTCHA tokens stay in component memory, buttons never auto-resubmit, and failure resets the challenge while preserving the typed email.

- [x] **Step 4: Run focused tests and confirm GREEN**

Run the Step 2 command.

Expected: PASS.

- [x] **Step 5: Commit the frontend protection slice**

```bash
git add new_frontend/src/api new_frontend/src/features/auth new_frontend/src/i18n new_frontend/src/styles
git commit -m "feat: add auth captcha and retry countdowns"
```

### Task 5: Supabase, Nginx, and Deployment Configuration

**Files:**
- Modify: `infra/supabase/compose.cloud.yaml`
- Modify: `infra/supabase/compose.staging.yaml`
- Modify: `infra/supabase/.env.example`
- Modify: `infra/supabase/cloud.env.example`
- Modify: `deploy/agent/cloud.backend.env.example`
- Modify: `new_backend/.env.example`
- Modify: `deploy/agent/host-nginx-agent-aliyun.conf`
- Modify: `deploy/agent/host-nginx-agent-split.conf`
- Modify: `deploy/agent/host-nginx-agent-staging.conf`
- Modify: `deploy/agent/stack.sh`
- Modify: `deploy/agent/scripts/prepare_staging.py`
- Modify: `deploy/agent/scripts/staging_preflight.py`
- Modify: `deploy/agent/scripts/install_host_nginx_agent_cloud.sh`
- Modify: `deploy/agent/tests/test_cloud_config.py`
- Modify: `deploy/agent/tests/test_aliyun_return_ingress.py`
- Modify: `deploy/agent/tests/test_split_cloud_ingress.py`
- Modify: `deploy/agent/tests/test_staging_compose.py`
- Modify: `deploy/agent/tests/test_prepare_staging.py`
- Modify: `deploy/agent/tests/test_staging_stack.py`
- Modify: `deploy/agent/tests/test_staging_nginx.py`
- Modify: `deploy/agent/tests/test_single_postgres_stack.py`
- Create: `deploy/agent/tests/test_auth_ingress_limits.py`
- Create: `deploy/agent/tests/test_host_nginx_auth_limits.py`
- Modify: `deploy/agent/DEPLOYMENT.md`
- Modify: `deploy/agent/STAGING.md`
- Modify: `infra/supabase/PSKIT.md`

**Interfaces:**
- Consumes: `X-PSKit-Client-IP`, backend/Turnstile settings, and GoTrue environment variables.
- Produces: dry-run Nginx auth zones with JSON 429, loopback-safe proxy headers, explicit GoTrue limits, and operational enable/disable procedures.

- [x] **Step 1: Write failing Compose and Nginx render tests**

Assert exact GoTrue values, secret placeholders without printed secret values, backend Turnstile/HMAC/trusted-proxy settings, production startup failure before `up -d` when required values are absent/placeholders, staging generation/preflight isolation, per-route auth zones in active/legacy/staging templates, global mail zone, `limit_req_dry_run on`, `$remote_addr` overwrite, JSON 429 and `Retry-After: 60`, direct verify header, and no auth limits on SSE/uploads/normal APIs/private admin/old site. Assert installer still executes `nginx -t` and restores the previous config on failure. The Docker-backed Nginx test must show forged client headers are overwritten, the signup burst eventually receives JSON 429, and an ordinary API path remains unaffected.

- [x] **Step 2: Run focused tests and confirm RED**

Run: `python -m pytest deploy/agent/tests/test_cloud_config.py deploy/agent/tests/test_aliyun_return_ingress.py deploy/agent/tests/test_split_cloud_ingress.py deploy/agent/tests/test_staging_compose.py deploy/agent/tests/test_prepare_staging.py deploy/agent/tests/test_staging_stack.py deploy/agent/tests/test_staging_nginx.py deploy/agent/tests/test_single_postgres_stack.py deploy/agent/tests/test_auth_ingress_limits.py deploy/agent/tests/test_host_nginx_auth_limits.py -q`

Expected: FAIL because explicit GoTrue variables and route zones are absent.

- [x] **Step 3: Add explicit inner limits and dry-run edge controls**

Set:

```env
GOTRUE_SMTP_MAX_FREQUENCY=60s
GOTRUE_RATE_LIMIT_EMAIL_SENT=60
GOTRUE_RATE_LIMIT_OTP=30
GOTRUE_RATE_LIMIT_VERIFY=30
GOTRUE_RATE_LIMIT_TOKEN_REFRESH=150
GOTRUE_RATE_LIMIT_HEADER=X-PSKit-Client-IP
```

Define Nginx keys only for mail, login, and verify paths; configure 6r/m burst 3, 10r/m burst 5, and 30r/m burst 10 respectively, plus a 2r/s burst 10 site mail budget and an auth-only per-IP connection ceiling of 10. Keep dry-run enabled and translate native rejects to `{"detail":{"code":"AUTH_RATE_LIMITED"}}` with `Retry-After: 60`. Inline zones/maps in each independently installed vhost and use distinct staging zone names so production and staging can coexist. Add PII-free `$limit_req_status` logging, production/staging preflight checks, and deployment notes for reviewing 24–48 hours before enforcement. Keep GoTrue global CAPTCHA disabled because Python validates only the three protected mail endpoints. Keep `CLOUD_DISABLE_SIGNUP=true` in examples until real SMTP, frontend site key, Python secret, and canary checks are installed; document the later controlled switch to `false`.

- [x] **Step 4: Run focused tests and confirm GREEN**

Run the Step 2 command.

Expected: PASS without changing public admin isolation or loopback bindings.

- [x] **Step 5: Commit the deploy configuration slice**

```bash
git add infra/supabase deploy/agent new_backend/.env.example
git commit -m "feat: configure layered public auth limits"
```

### Task 6: Generated Contract and Full Regression

**Files:**
- Modify: `contracts/openapi.json`
- Modify: `new_frontend/src/api/generated/types.gen.ts`
- Modify: `new_frontend/src/api/types.contract.test.ts`
- Modify: `docs/superpowers/plans/2026-10-06-public-registration-abuse-protection.md`

**Interfaces:**
- Consumes: completed backend DTOs and all previous slices.
- Produces: synchronized OpenAPI/TypeScript contracts and verification evidence.

- [x] **Step 1: Add a failing generated-contract assertion**

Assert `captcha_token` exists on signup, recovery, and guest email upgrade start, and does not exist on guest email verification.

- [x] **Step 2: Run contract tests and confirm RED before generation**

Run: `cd new_frontend && npm test -- src/api/types.contract.test.ts`

Expected: FAIL against the old generated schema.

- [x] **Step 3: Export OpenAPI and regenerate TypeScript**

Run:

```bash
cd new_backend
PYTHONPATH=. .venv/bin/python scripts/export_openapi.py
cd ../new_frontend
npm run generate:api
```

Do not manually edit generated files.

- [x] **Step 4: Run complete verification**

Run:

```bash
cd new_backend
TEST_POSTGRES_DSN=postgresql://postgres:pskit-test-only@127.0.0.1:15433/postgres PYTHONPATH=.:.. .venv/bin/python -m pytest tests -q --tb=short
.venv/bin/ruff check app tests scripts
.venv/bin/python -m compileall -q app scripts
cd ../new_frontend
npm test
npm run typecheck
npm run lint
npm run build
cd ..
python -m pytest deploy/agent/tests -q
```

Expected: all tests and static checks pass; build succeeds; no test contacts real SMTP or Turnstile.

- [x] **Step 5: Record evidence and commit generated contracts**

Update the task checkboxes and append the exact passing command summaries without secrets.

```bash
git add contracts/openapi.json new_frontend/src/api/generated/types.gen.ts new_frontend/src/api/types.contract.test.ts docs/superpowers/plans/2026-10-06-public-registration-abuse-protection.md
git commit -m "test: verify public auth abuse protection"
```

### Task 7: Whole-Branch Security and Specification Review

**Files:**
- Review only: all files changed since `f5fe84a`
- Modify only when a review finding requires a fix.

**Interfaces:**
- Consumes: all completed tasks and verification output.
- Produces: independent spec/standards review with every P0/P1 resolved before completion.

- [x] **Step 1: Build a review package from the implementation-plan base**

Include the spec, plan, commit range, verification output, and deployment boundary.

- [x] **Step 2: Run an independent whole-branch review**

Review for spec coverage, transaction races, spoofed-IP behavior, refund classification, PII leakage, CAPTCHA fail-closed behavior, route scope, generated-contract drift, and paid-service scope creep.

- [x] **Step 3: Fix findings with TDD and rerun affected plus complete verification**

Expected: zero unresolved P0/P1 findings and no new paid Alibaba DDoS/WAF configuration.

- [x] **Step 4: Commit review fixes, if any**

```bash
git add <reviewed-fix-files>
git commit -m "fix: resolve auth protection review findings"
```

## Verification Evidence

- Backend: `714 passed, 1 skipped` against the local PostgreSQL test service; no test used real SMTP or Turnstile.
- Backend static gates: repository-wide Ruff passed and `compileall` completed successfully.
- Frontend: `240 passed`; TypeScript typecheck, ESLint, and the Vite production build passed. Vite retained the existing large-chunk warnings.
- Deployment: `126 passed, 3 skipped` with existing local Docker images; focused auth/deployment contracts passed `64 passed` before the final review fixes.
- Review fixes: the site-wide mail budget now uses a vhost-wide key; direct and guest-upgrade verification routes share the verify ceiling; every general API proxy overwrites the private client-IP header; production preflight rejects the public Turnstile always-pass test keys and any trusted-proxy range broader than local Nginx.
- Scope: no Alibaba Cloud paid DDoS/WAF service, Redis, public backend port, direct browser-to-Supabase auth call, or GoTrue global CAPTCHA was added.
