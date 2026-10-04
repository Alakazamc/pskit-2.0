# PSKit 管理台 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 管理员使用 Supabase 身份，在 React 管理台配置模型开放、科研服务、用户额度，并查看沙箱、作业和对账。

**Architecture:** 管理页只通过 Python JWT/RBAC API。LiteLLM 保 provider/key/部署；PSKit 保发布和用户授权策略。每次真实调用重新授权，配置和额度更新通过事务、版本与审计执行。

**Tech Stack:** 现有 React/Vite/Radix/TanStack Query、FastAPI、Supabase 验证、PostgreSQL。

**Spec:** [统一对接方案](/home/jhli/pskit-2.0/docs/research/2026-10-04-sandbox-compute-integration-proposal.md)，第 7、8 节。

## Global Constraints

- 继承 [总计划](/home/jhli/pskit-2.0/docs/superpowers/plans/2026-10-04-sandbox-compute-admin.md)全部约束；使用 A 的 SandboxOperations 和 B 的 ComputeJobs/ComputeLedger。
- 管理角色：platform_admin、service_maintainer、quota_operator、auditor；不接受用户可编辑 metadata 作为角色来源。
- 不向浏览器发送 X-Admin-Key、LiteLLM master key、Supabase service role；已有 key 运维接口保兼容并审计。
- 模型策略启用显式 `managed` 模式；Staging 完成发布/授权验证后再切换生产，不让空新策略使当前生产选择器突然清空。
- 更新带 expected_revision/reason；配置与审计原子提交。限制减少不改 used/reserved。
- 中英双语、明暗主题、键盘操作、窄屏；输入样式遵守 new_frontend/AGENTS.md，不新增 UI 框架。

## Review Focus

- 管理员撤权后旧 JWT：C-1 API 立即拒绝。
- 伪造被隐藏 alias/工具页 LLM 请求：C-2 真实执行拒绝。
- 维护者修改别人的服务、schema 更新自动开放：C-3 拒绝或保草稿。
- 同时修改限额与账本对账：C-4 冲突可解释、used/reserved 保留、审计原子。
- 401/403、空目录、取消未确认和长列表：C-5/C-6 页面不假成功且可操作。

---

### C-1: JWT 管理员权限与受控初始化

**Files:** 新建 `app/contracts/admin.py`、`app/domain/admin/roles.py`、`app/api/admin_auth.py`、`app/commands/grant_admin.py`、`app/db/postgres_migrations/006_admin.sql`；修改 migrations、`app/api/admin.py`、`app/main.py`；测试 `tests/test_admin_identity.py`、`tests/postgres/test_admin_roles.py`。

**Interfaces:** `AdminPrincipal(user_id, roles, scopes)`、`require_permission(permission, resource_owner=None)`；`GET /api/v1/admin/me` 返回角色与允许动作。初始化命令 `.venv/bin/python -m app.commands.grant_admin --user-id <verified-id> --role platform_admin` 只在服务器执行，记录操作者来源与审计。

- [x] **Red:** `test_user_cannot_self_assign_admin`：metadata admin=true 仍 403；`test_revoked_admin_is_rejected_with_old_token`：撤权后原 token 立即 403；`test_maintainer_scope_does_not_grant_quota_write`：维护者限额写入 403。
- [x] **Run:** `.venv/bin/python -m pytest tests/test_admin_identity.py tests/postgres/test_admin_roles.py -q`，逐个先失败。
- [x] **Green:** 复用 CurrentUserDep，查当前服务器角色/范围；旧 X-Admin-Key 仅用于受控运维兼容，不改变浏览器认证路径。
- [x] **Verify:** 无登录 401、游客 403、审计员写操作 403；响应不含密钥/原始 JWT。
- [x] **Commit:** 已合入后端批次 `06c1685`；共享迁移、应用组装和生成契约按完整依赖一起提交。

### C-2: LLM alias 发布与真实执行授权

**Files:** 新建 `app/domain/admin/model_policy.py`、`app/api/admin_models.py`；修改 `app/api/models.py`、`app/api/workspace.py`、`app/api/internal.py`、`app/services/model_catalog.py`、`app/config.py`；测试 `tests/test_admin_model_policy.py`、`tests/test_model_authorization.py`。

**Interfaces:** `ModelPolicy.visible_for(user_id, purpose) -> list[ModelOption]`、`authorize(user_id, alias, purpose) -> PublishedModelPolicy`；`GET /admin/llm-aliases`、`PUT /{alias}/draft`、`POST /{alias}/publish`。网关发现与当前用户策略分开缓存。

- [x] **Red:** `test_new_gateway_alias_is_draft_until_published`；`test_hidden_alias_cannot_be_sent_by_api`：列表无 alias，伪造 send 返回 403；`test_policy_revocation_applies_to_next_model_call`：当前已启动调用按策略结算，下一调用被拒。`test_gateway_fallback_does_not_bypass_publication`：发现失败不能恢复已撤销默认 alias。
- [x] **Run:** `.venv/bin/python -m pytest tests/test_admin_model_policy.py tests/test_model_authorization.py -q`。
- [x] **Green:** 目录交集、用途和用户/组授权；发布引用既有 LiteLLM alias，支持图片/推理只收窄，服务端发送/内部代理都查当前策略。明确空 allowlist 为无授权。
- [x] **Verify:** 模型选择、图片/推杆现有测试保留；工具页分析同样受到 alias 权限与 Token 预占。
- [x] **Commit:** 已合入后端批次 `06c1685`；共享迁移、应用组装和生成契约按完整依赖一起提交。

### C-3: 科研服务草稿、发现与发布

**Files:** 新建 `app/api/admin_services.py`、`app/domain/admin/releases.py`；修改 B 的 catalog、`app/domain/catalog.py`；测试 `tests/test_admin_compute_services.py`、`tests/postgres/test_config_releases.py`。

**Interfaces:** `/admin/services`、`/{id}/discovery`、`/{id}/checks`、`/capabilities/{id}/draft`、`/config-releases/{id}/publish`；`ConfigReleaseService.publish(actor, versions, expected_revision, reason) -> ConfigRelease`。endpoint_ref/credential_ref 仅引用服务器批准位置。

- [x] **Red:** `test_maintainer_can_edit_only_owned_service`：自己的草稿可改，别人的 403，发布需管理员；`test_discovered_schema_change_does_not_mutate_published_capability`：digest 更新先生成候选；`test_connectivity_check_does_not_run_inference`：仅连接/schema 检查不触发模型。
- [x] **Run:** `.venv/bin/python -m pytest tests/test_admin_compute_services.py tests/postgres/test_config_releases.py -q`。
- [x] **Green:** 不可变能力版本、draft/validated/published/retired、授权范围、真实计算检查的明确测试身份/资源上限；Skill 引用已发布 capability；服务地址受允许网络区约束。
- [x] **Verify:** expected_revision 冲突 409；发布/审计/outbox 同事务；旧 Job 保存旧策略，新提交查新策略。
- [x] **Commit:** 已合入后端批次 `06c1685`；共享迁移、应用组装和生成契约按完整依赖一起提交。

### C-4: 原子额度、作业取消与计量对账

**Files:** 新建 `app/api/admin_operations.py`、`app/domain/admin/audit.py`；修改 `app/api/admin.py`、B ledger、A operations；测试 `tests/test_admin_limits.py`、`tests/postgres/test_admin_compute_operations.py`、`tests/postgres/test_admin_audit.py`。

**Interfaces:** `update_limits(actor, user_id, limits, expected_revision, reason) -> UserLimits`；`GET /admin/users|jobs|sandboxes|usage/reconciliation|audit-events`、`POST /jobs/{id}/cancel`、`/compute/jobs/{id}/reconcile`、`/sandboxes/{id}/drain`。分页使用 items/next_cursor，审计包含旧/新值摘要不含 secret。

- [x] **Red:** `test_limit_update_is_atomic_and_audited`：故意使审计写入失败，限额也不改变；`test_lower_limit_keeps_existing_reservation`：已用+预占大于新限额仍保留；`test_cancel_ui_status_is_not_stopped_until_confirmed`；`test_reconcile_requires_reason_and_terminal_evidence`。
- [x] **Run:** `.venv/bin/python -m pytest tests/test_admin_limits.py tests/postgres/test_admin_compute_operations.py tests/postgres/test_admin_audit.py -q`。
- [x] **Green:** 复用业务动作，不开放任意表 CRUD；待停止/待对账独立状态；限额与审计同事务；管理员不因角色自动获得所有科研输入正文。
- [x] **Verify:** 旧 AF3 对账和配额 API 兼容；冲突不部分更新；取消后只在实际执行确认后释放额度。
- [x] **Commit:** 已合入后端批次 `06c1685`；共享迁移、应用组装和生成契约按完整依赖一起提交。

### C-5: React 管理入口与模型/服务页面

**Files:** 新建 `new_frontend/src/features/admin/AdminShell.tsx`、`AdminModelsPage.tsx`、`AdminServicesPage.tsx`、`adminQueries.ts`、`new_frontend/src/api/admin.ts`；修改 `src/app/App.tsx`、`src/api/types.ts`、`src/api/client.ts`、`src/i18n/translations.ts`；测试 `src/features/admin/AdminAccess.test.tsx`、`AdminModelsPage.test.tsx`、`AdminServicesPage.test.tsx`。

**Interfaces:** `ResearchApi.getAdminMe/listAdminModels/saveModelDraft/publishModel/listServices/saveServiceDraft/checkService/createConfigRelease/publishConfigRelease` 对接生成类型；/admin 路由与已有用户会话共享 auth/query，不存 admin key。服务页面在能力草稿验证后显示发布版本与影响摘要，调用 C-3 发布包，不直接修改已发布 manifest。

- [x] **Red:** `ordinary user cannot open management`；`admin can publish model and see updated state`；`maintainer can submit owned service without a publish action`；`failed save retains form and displays localized error`。mock 只替 HTTP，数据通过契约生成，不硬编码进页面。
- [x] **Run:** `cd new_frontend && npm run test -- src/features/admin/AdminAccess.test.tsx src/features/admin/AdminModelsPage.test.tsx src/features/admin/AdminServicesPage.test.tsx`。
- [x] **Green:** 复用当前主题/Radix，API 层统一请求、TanStack Query 管服务状态；仅 UI draft/selection 用本地状态。中英文新增 key、空列表/草稿/冲突/权限文案齐全。
- [x] **Verify:** 上述测试、typecheck、lint、build；键盘与手机布局可操作，输入不出现禁用的白色框。
- [x] **Commit:** 已合入前端批次 `37a01ff`；共享迁移、应用组装和生成契约按完整依赖一起提交。

### C-6: 用户额度、沙箱/作业和审计页面

**Files:** 新建 `src/features/admin/AdminUsersPage.tsx`、`AdminJobsPage.tsx`、`AdminSandboxesPage.tsx`、`AdminUsagePage.tsx`、`AdminAuditPage.tsx`；修改 admin API、translations、路由；测试 `AdminOperations.test.tsx`；新建 `deploy/agent/scripts/admin_smoke.py`。

**Interfaces:** 查询 API 使用 C-4 的分页合约；动作发送 expected_revision/reason，返回 request/operation 状态；授权变化后刷新 AdminMe，403 时清理相应查询并退出允许操作。

- [x] **Red:** `quota edit preserves used and reserved`；`cancel shows requested then confirmed`；`auditor has no mutation controls`；`403 removes stale privileged actions`；`empty and paged lists remain usable in both languages`。
- [x] **Run:** `npm run test -- src/features/admin/AdminOperations.test.tsx`，逐个行为先 red。
- [x] **Green:** 明确 used/reserved/remaining、CPU 核秒/GPU 分钟、未知/待对账；分页列表和排空状态；运营按钮调用业务 API，不做乐观“已停止”提示。
- [x] **Verify:** 前后端相关测试、typecheck/lint/build、OpenAPI 类型生成；本地隔离 PostgreSQL 用合成管理员/维护者/普通用户通过真实 HTTP 验收授权、服务发布、限额和任务停止确认；浏览器只替换 HTTP 边界，覆盖双语/双主题/桌面/窄屏。生产数据/密钥不进入 mock；远程 Staging 部署另走发布流程。
- [x] **Commit:** 已合入前端批次 `37a01ff`；共享迁移、应用组装和生成契约按完整依赖一起提交。

## 实施记录

2026-10-04 用户授权管理前端与后端并行。C-1～C-4 和 C-5～C-6 先按共享 API 合约分别实施，再整合生成类型与真实 HTTP/PostgreSQL 验证。局部红绿及审查修复已记录；完整回归与提交统一记入 [交付记录](../../research/2026-10-04-sandbox-admin-delivery.md)。本轮未更新远程 Staging 或生产。
