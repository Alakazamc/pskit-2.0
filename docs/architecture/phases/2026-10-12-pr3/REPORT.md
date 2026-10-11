# PR-3 qualification implementation evidence

Base HEAD: `eb8df7027e85926145ac541a1c446b45104c2a08` (PR-0/PR-1 remote CI gate verified by root).
All checks here are offline: Mock HTTP/SDK transports, isolated temporary SQLite, pinned containers with `--network none`, in-memory Pi fetch. No paid providers, SSH, deployment, settings changes, real database or PG test overlap.

## Nested caller parameter denial — latest frozen source

Pinned LiteLLM/OpenAI SDK wire evidence showed that caller `extra_body` merges after the validated top-level request: `max_tokens=8` became 1,000,000, `tools` became empty, `tool_choice:auto` became none, and reasoning high became none. Read-only reproduction remains in `../pr3-readonly-review/extra_body_probe.py` and `.log`. The qualification Workspace proxy previously forwarded this wrapper, bypassing its bounded/strict request intent.

Added eight negatives for those four nested fields with False/True policies. RED: `red-extra-body-qualification.log` **8 failed / 45 deselected in 2.11s**, all previously accepted with 200. The one-field driver denylist addition rejects caller `extra_body` before budget admission or upstream dispatch with the existing 403 `MODEL_QUALIFICATION_FAILED` contract. Tests require no provider request, admission, new Run/guard, token entry, or usage-table change. Only the qualification driver and its existing tests changed for this correction; peer owns the corresponding ordinary proxy denial and tests. No helper, contract, global configuration or Git changes by this child.

`parameter-wrapper-source.log` records an offline cached pinned SDK interface/source check: `extra_body`, `extra_headers`, `extra_query` are the exposed request wrappers; `extra_body` merges `options.extra_json` into the JSON body, while headers/query are processed separately. No additional same-JSON-body wrapper was established in that interface, so the denylist patch remains limited to the demonstrated field.

Latest verification supersedes previous focused totals:

- `green-extra-body-qualification.log`: **8 passed / 45 deselected in 1.18s**.
- `green-extra-body-owned-focused.log`: **172 passed in 21.03s**, complete qualification scope including actual pinned Pi parser.
- `green-extra-body-focused.log`: **212 passed in 28.68s**, qualification/Pi plus peer ordinary/shared-policy tests; zero failures/skips, started after peer source freeze.
- `ruff-extra-body.log`: all checks passed; `compile-extra-body.log`: exit 0.
- Frozen seven qualification-owned source/test files: `frozen-extra-body-source-sha256.txt`.
- Root/peer dependencies captured before the final combined run and verified unchanged afterward: `extra-body-dependency-sha256.txt` and `extra-body-dependency-verify.log`.

The initial combined run imported peer's RED tests before its ordinary proxy patch: `pre-peer-extra-body-focused.log` **8 peer failures / 204 passed in 33.77s**. That interim run is retained as failure evidence and is not counted as GREEN. Parent owns refreshed full/remote CI and Git integration for the final source.

## Optional tool-choice parameter proof — prior correction retained

Default False previously recorded `tool_choice:required`/`tool_choice:specific` as passed when a genuine tool response was returned, although the selected choice itself lacked unchanged-wire proof. Added six negatives covering both omitted/default False and explicit False domain policies, plus actual driver/ledger captures. RED: `red-optional-choice-parity.log` **6 failed / 127 deselected in 2.37s**.

The minimal domain guard records those two optional cases as `not_tested`, with `not_tested_reason="strict_parameter_forwarding_unverified"`, before budget authorization or probe/provider dispatch. Core `auto` still runs on the existing runtime wire. Explicit True still verifies required/specific independently, including provider rejection; the existing optional-mode failure test now explicitly opts in. Trusted staging fixtures prove that the unchanged AgentReady required set and default/explicit False profile readiness are preserved. No global `drop_params`, helper, runtime driver, contract, root/peer source or Git change was made for this correction.

Latest verification supersedes previous focused totals:

- `green-optional-choice-parity.log`: **6 passed / 127 deselected in 2.37s**.
- `green-optional-choice-focused.log`: **196 passed in 32.54s**, all focused qualification, actual pinned Pi parser and peer shared-policy/ordinary-proxy cases; zero failures/skips.
- `ruff-optional-choice.log`: all checks passed; `compile-optional-choice.log`: exit 0.
- Frozen seven qualification-owned source/test files: `frozen-optional-choice-source-sha256.txt`.
- Root/peer policy dependencies read during that verification: `optional-choice-dependency-sha256.txt`.

Only the qualification domain and its matrix/driver tests changed after the previous freeze. Parent owns the refreshed complete/remote CI and Git integration.

## Qualification/runtime parameter parity — prior correction retained

Independent review found that qualification forced parameter passthrough while ordinary Pi proxy calls did not. This could qualify a request shape different from the runtime shape. RED before this driver correction: `red-runtime-parity.log` **6 failed / 37 deselected in 8.51s**, including real ordinary HTTP proxy versus qualification wire captures for both policy values and native/mixed-route refusal.

Root added `ModelWirePolicy.preserve_openai_parameters: bool=False`, keeping the exact legacy fingerprint when False/absent and including True in the candidate fingerprint. Root owns that contract/fingerprint change. Peer `pr2_summaries` owns `prepare_model_parameters(payload, profile)` in `model_gateway.py`, its ordinary proxy application, caller override denial and `test_model_parameter_policy.py`.

Qualification domain requests and actual driver transport now use the **same helper** as ordinary proxy calls. False does not add/remove the private passthrough keys or change existing global gateway defaults. True adds the pinned, verified forwarding policy to both paths. Qualification verifies every discovered route belongs to the offline-proven `openai/` or `custom_openai/` adapter family before any provider dispatch/reservation; an opted-in native/mixed route fails closed. False/unsupported native reasoning remains `not_tested` with the existing reason. Ordinary and qualification proxy callers cannot supply the server-owned private policy flags.

Strict qualification test profiles now explicitly opt in. Existing published False/absent profiles retain their exact old candidate hashes and evidence compatibility. Core tools on the legacy wire may still pass when the genuine typed call/continuation/stream/artifact checks succeed; this correction does not change the legacy required set or global defaults.

Verification of that correction (superseded by the 196-test run above):

- `green-runtime-parity.log`: **6 passed / 37 deselected in 4.84s**.
- `green-shared-policy-focused.log`: **190 passed in 27.13s**, qualification/domain/driver/matrix/wiring, actual pinned Pi parser and peer shared-policy/ordinary-proxy cases.
- `ruff-shared-policy.log`: all checks passed; `compile-shared-policy.log`: exit 0; owned `git diff --check`: exit 0.
- Frozen seven qualification-owned source/test files: `frozen-shared-policy-source-sha256.txt`.
- Root/peer policy dependency contents verified in that run: `shared-policy-dependency-sha256.txt`.

No contract file was edited by this child for the parity correction. Root owns generated contracts and refreshed full/remote CI for the combined source.

## Independent review correction — prior correction retained

Review integration baseline at verification: `f16a09cecf9a077deec9787e943107bbf9dabe51`.
Only `model_qualification.py` and `test_model_qualification_matrix.py` changed after the initial PR-3 freeze.

The review identified missing nonstream envelope/terminal validation. Added 54 cases across text, image, reasoning, continuation, tools and automatic tool choice. RED first: `red-nonstream-review.log` **42 failed / 13 passed / 29 deselected in 0.65s**. Centralized `_message` now rejects an explicit error envelope, bool/float/nonzero choice index, and present invalid terminal values. Present text terminal must be `stop`; present tool terminal must be `tool_calls`; explicit null is invalid. Legacy absent index/finish fields and correct terminal responses remain accepted, without changing profile fingerprints or existing qualification evidence.

Latest verification:

- `green-nonstream-review.log`: **55 passed / 29 deselected in 0.42s**.
- `green-reviewed-focused.log`: **152 passed in 13.90s**, all scoped domain/driver/matrix/wiring and actual pinned Pi parser checks.
- `ruff-reviewed.log`: all checks passed.
- `compile-reviewed.log`, `diffcheck-reviewed.log`: exit 0.
- Exact frozen owned-file contents: `frozen-reviewed-source-sha256.txt`.

Earlier GREEN evidence below remains relevant for unchanged native LiteLLM/PinCC wire checks. Root's prior full CI snapshot predates this review correction and is not verification of the latest candidate; root owns the refreshed broad/remote gates.

## Original owned files

1. `apps/api/app/domain/admin/model_qualification.py`
2. `apps/api/app/services/model_qualification_driver.py`
3. `apps/api/app/contracts/model_runtime.py`
4. `apps/api/tests/test_model_qualification.py`
5. `apps/api/tests/test_model_qualification_driver.py`
6. `apps/api/tests/test_model_qualification_matrix.py` (new)
7. `apps/api/tests/test_model_qualification_pi_stream.py` (new)
8. `apps/api/tests/qualification_litellm_wire.py` (new, standalone pinned-gateway unittest)

This child did not edit gateway runtime, Pi runtime/config, provider compatibility hook, LiteLLM global config, policy/ledger services, or model registry. The latest shared parameter helper/ordinary proxy changes are peer-owned as described above, and the new profile opt-in/fingerprint change is root-owned.

## Behavior

- Fixed requests independently record `tool_choice:auto`, `tool_choice:required`, `tool_choice:specific`. Core tool/stream checks exercise auto, as Pi does. Required/specific remain optional because current profiles declare neither mode: False/absent records not tested with the parameter-proof reason; True verifies actual requests and keeps provider failures visible. No new tool-choice capability flag is added and the existing `agent_ready` required set is unchanged. Root's parameter-preservation opt-in binds True to a new fingerprint while preserving exact legacy False/absent hashes and valid evidence.
- Streaming qualification rejects changed IDs/names, fragmented names, wrong/bool tool index, wrong choice index, extra choices/calls, terminal error/length before a later valid finish, malformed/truncated arguments and unverified empty chunks. Repeated identical identities and nullable optional SDK fields remain accepted. Only arguments append.
- Transport rejects missing, repeated, incomplete or followed-by-data `[DONE]`, and explicit streamed error envelopes. Existing terminal usage ambiguity check remains.
- Nonstream checks reject empty/whitespace text, extra tool calls, explicit error envelopes, malformed choice indices and invalid present terminal state; omitted legacy index/finish fields remain compatible. Vision uses a decodable fixed 32×32 red PNG and requires exact JSON `{"color":"red"}`. Prompt does not disclose the color. Empty, generic and incorrect recognition fail.
- Alias routing now makes a real completion with the alias. Returned gateway model ID must be in the captured deployment snapshot, with the expected gateway version/API URL, and a usable text response. Direct deployment probes still cover every route.
- Explicitly opted-in transport requests apply the shared policy, including authenticated workspace proxy requests. Caller `extra_body` and private forwarding overrides are denied before quota/provider admission. Pinned LiteLLM 1.100.3 has an OR with global `litellm.drop_params=True`, so request `drop_params=False` alone does not prevent dropping. For verified `openai/` and `custom_openai/` adapters, shared ordinary/qualification `allowed_openai_params` preserves tools, tool choice and reasoning effort, with `additional_drop_params=[]`; provider rejection is retained as failure. Both native SDK families and Router deployment overrides are covered offline. Legacy False retains the runtime's existing mapping rather than forcing a qualification-only override.
- False/non-preserving and native/other mapped reasoning routes lack the same strict wire proof. Reasoning cases are explicitly `not_tested`, with evidence-only `not_tested_reason="strict_parameter_forwarding_unverified"`. This additive field defaults to null and does not affect candidate fingerprints. Opted-in native/mixed route discovery is rejected before calls. Root owns generated contract updates.
- Public Responses rejection is `MODEL_PROTOCOL_UNSUPPORTED`.
- Existing unknown/timeout usage hold, attempt/token bounds, cancellation settlement, audit ownership, route/profile drift checks and actual Pi Workspace artifact path are preserved. Offline/injected drivers still cannot grant Agent Ready to real providers.

## RED evidence before associated runtime fixes

| Log | Result |
|---|---|
| `red-matrix.log` | 21 failed / 6 passed: demonstrated false positives and missing independent mode records |
| `red-driver.log` | 4 failed / 3 passed / 26 deselected: alias never exercised; repeated/error/truncated DONE accepted |
| `red-sdk-nullable.log` | 3 failed / 27 deselected: initial strict implementation rejected valid nullable SDK fields; then minimally corrected |

Initial sandboxed threaded SQLite test runs (`red.log`, `green-matrix.log`) were interrupted after hanging; they are not counted as completed verification. The focused ledger suite completed under escalated local execution using isolated SQLite; no PG or network dependency was added.

## GREEN evidence

| Log | Result |
|---|---|
| `green-final-focused.log` | **98 passed in 13.04s**, qualification domain/driver/matrix/wiring plus actual pinned Pi parser |
| `green-pi-parser.log` | **2 passed in 0.82s**, real Pi 0.87.1 SDK with in-memory fetch; repeated identity + argument fragments accepted, fragmented name retained by Pi and rejected by qualification |
| `green-litellm-wire.log` | **4 unittest tests passed in 2.437s**, including 48 adapter/choice/effort/stream/sync-async wire combinations; global drop characterization, upstream 400, and Router deployment drop defaults |
| `green-pincc-openai-anthropic.log` | **5 unchanged compatibility unittest tests passed in 2.182s**, real pinned SDK transports; native PinCC patch remains restricted |
| `ruff.log` | All checks passed, final owned Python files |
| `compile.log` | compileall exit 0, final owned Python files |

`git diff --check` also passed for owned tracked changes.

## Reproduction / integration

Focused local command (Node and Pi paths below point to root's canonical installed pinned dependencies):

```sh
cd apps/api
PSKIT_TEST_PI_ROOT=/tmp/pskit-remediation-20261012/head/apps/api/pi \
PSKIT_TEST_NODE=/tmp/pskit-remediation-20261012/node22/node_modules/node/bin/node \
.venv/bin/python -m pytest tests/test_model_qualification.py \
  tests/test_model_qualification_matrix.py tests/test_model_qualification_driver.py \
  tests/test_model_qualification_pi_stream.py tests/test_model_qualification_wiring.py -q
```

The new Pi test defaults to the ordinary repo `apps/api/pi` installation and `node` when the test-only override variables are absent. No missing-Pi/socket skip was introduced.

Pinned strict wire command for the gateway CI step (root owns workflow):

```sh
docker run --rm --network none --read-only --tmpfs /tmp \
  -e LITELLM_LOCAL_MODEL_COST_MAP=True -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$PWD/apps/api/tests/qualification_litellm_wire.py:/qualification_wire.py:ro" \
  --entrypoint python ghcr.io/berriai/litellm:v1.100.3 /qualification_wire.py
```

Root still owns generated API/TS contracts, broad backend/PG17/actual Pi Workspace verification, integration commit/push and full remote CI. The standalone strict-wire script should be added to the existing gateway-protocol job. No qualification driver split was performed; it was optional after behavior acceptance and would broaden this patch.

## Runtime evidence source

Pinned Pi installed source: `head/apps/api/pi/node_modules/@earendil-works/pi-coding-agent/node_modules/@earendil-works/pi-ai/dist/api/openai-completions.js`, lines 297–345 and 425–439: first nonempty ID/name retained; arguments append. Actual SDK test confirms the behavior rather than merely matching source text.

Pinned LiteLLM source and parameter characterization: `litellm-strict-source-characterization.log`, `litellm-router-characterization.log`, `litellm-sse-source.log`; actual SDK wire tests provide the acceptance evidence.

## Remaining qualification limit

No new real supplier was tested or published. Native mapped reasoning remains not tested until its strict native wire semantics can be proven. Real provider Agent Ready still requires separately authorized staging, the exact profile/deployment fingerprints, real `write_file → read_file → artifact.created → authenticated download`, and settled usage. Existing published model profiles and evidence are not retroactively revoked by the optional mode matrix.

## Root integration and generated contracts

The integration also owns `services/model_gateway.py`, `api/internal.py`, `tests/test_model_parameter_policy.py`, `tests/test_model_wire_policy_compatibility.py`, `.github/workflows/new-stack.yml`, `contracts/openapi.json` and `apps/web/src/api/generated/types.gen.ts`. The shared helper is opt-in through the trusted profile. Private caller flags and `extra_body` are rejected before quota/provider dispatch. Native LiteLLM 1.100.3 wire captures demonstrate that `extra_body` could otherwise replace the capped top-level output limit and tool/reasoning values; both ordinary and qualification proxy negative cases reject it. No recursive input rewriting or global gateway switch is added.

Generated contracts were refreshed only in the two expected files. Public path definitions and every other component schema compare equal to the prior HEAD; SSE/Compute schemas and their generated TypeScript remain byte-identical. The two additive model fields are optional. Default/False profiles retain exact legacy candidate fingerprints (4 RED→GREEN compatibility checks); True participates in the hash.

Full locked frontend: **400 passed / 53 files**, typecheck/lint/build passed; Molstar remains lazy with no initial import. Contract regeneration passed. Commands and complete logs are under `/tmp/pskit-remediation-20261012/pr3-final-frontend/`, `pr3-final-frontend-console.log` and `pr3/green-final-contracts.log`. Full final backend/PG17 and exact remote job results are added to the final phase index.

## Rollback

Revert this stage and regenerate the two contract outputs. No schema/data migration, real profile update or paid provider configuration occurred. Old runtime contracts reject unknown JSON fields. Default False/None extension fields are omitted when serializing, so legacy profiles and ordinary evidence remain readable on rollback. Profiles/evidence that use nondefault True or an explicit new reason require a rollback artifact supporting those extensions; do not send their data to an arbitrary pre-extension binary or reuse qualification evidence for a different transport. Existing default/False fingerprints were proven unchanged. The earlier PR-2 and MCP repairs are independent and remain applied. Real provider/staging/host qualification remains not tested in this task.

## Default-value persistence and rollback review

Prior `RuntimeContract` uses `extra="forbid"`; new defaults would otherwise enter persisted profile/Run/evidence JSON even when disabled. Six new compatibility checks first failed, then all **12 wire-policy compatibility checks passed** after conditional serialization omits only `preserve_openai_parameters=False` and `not_tested_reason=None`. Existing/nondefault fields and explicit True/reason are retained; tests explicitly prove the latter require supporting contracts. Legacy default candidate hashes still match. No stored data was rewritten.

Broad pre-serialization snapshot: **1489 passed / 1 existing external Supabase skipped, PG232 / 0 skipped**, 334.27s. It includes all shared-policy, optional-mode, nonstream and extra_body fixes but predates this default-value serialization review. Final focused, pinned-runtime and remote CI evidence verifies the last change; the final combined full gate is indexed separately. Do not treat the earlier broad snapshot as the final contract SHA.

The local venv uses Pydantic 2.13.5; locked CI/runtime uses 2.14.0. Both support the conditional Field serializer. Pinned-runtime compatibility and image verification are recorded separately, and the remote workflow installs the exact constraints.

Final serialized candidate: **228 passed / 0 skipped** in 33.74s (`pr3/green-serialization-focused.log`), including qualification domain/driver, actual Pi parser, ordinary proxy policy, legacy fingerprints/serialization, and Run context. Fixed runtime Pydantic 2.14.0 loads the full prior contract and passes 11 additional Python/JSON/profile/evidence/Run compatibility checks. Generated contracts still match (`green-serialized-contracts.log`). One earlier invocation named nonexistent test paths and ran no tests; retained as `serialization-invocation-missing-file.log` and excluded from GREEN counts.

Preceding PR-2/MCP exact HEAD `9da18a1d783f9b3d5915cb0903d675e52020dda1`: all six remote jobs passed in [New stack CI](https://github.com/Alakazamc/pskit-2.0/actions/runs/38160933404). This stage's final commit and workflow link are in the final index, rather than reusing that predecessor result.
