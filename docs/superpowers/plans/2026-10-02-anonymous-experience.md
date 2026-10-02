# 匿名科研体验实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking. 本项目不安排子代理；每个切片独立进行红、绿、整理与审查。

**Goal:** 让游客在 Python/Supabase 身份边界下有限量地聊天、建项目和上传文件，并能升级成保留数据的新账号。

**Architecture:** Supabase 原生匿名用户沿用现有 `user_id` 所有权；Python 持久保存账户等级，并在 HTTP 与后台任务入口共享能力及配额策略。React 只调用 Python API；游客绑定新身份时保留原 Supabase 用户 ID。

**Tech Stack:** FastAPI、Pydantic、httpx、SQLite、Supabase Auth、Pi RPC、React/Vite/TypeScript、pytest/httpx、Vitest。

**Spec:** `docs/superpowers/specs/2026-10-02-anonymous-experience-design.md`

## Global Constraints

- 游客默认每月 20,000 Token、总上传 10 MiB、单文件 2 MiB、每日 GPU 0 分钟、最多 1 个并发 Agent Run；设置可配。
- 游客不能使用 GPU/AF3。MCP 对游客默认拒绝，低成本无副作用工具须显式允许。
- Python 是浏览器唯一 API 入口；生产身份从 Supabase 服务端验证结果取得。不要增加前端 Supabase SDK；live 访问令牌只放内存，现有 mock 开发模式可沿用其本地持久方式。
- 保留同一 Supabase 用户 ID 的新邮箱/Google 升级；首版不合并已有账号，不通过 IP、指纹或文件哈希认领数据。
- 模型网关、真实 Supabase 和计算节点未部署时只报告替身契约验证，不称为真实联调。
- 先写失败测试，再写最小实现；API 变化更新 OpenAPI 与前端生成类型。不要改变旧 `backend/`、`frontend/`。

## Review Focus

1. 两个并发游客创建请求共用同一客户端地址：原子计数只能放行配置数量，超出的返回 429；见任务 3。
2. 游客通过直接 AF3、Pi 内部提交或审批尝试绕过页面限制：均返回 `LOGIN_REQUIRED`，无任务与 GPU 预留；见任务 5。
3. 两个并发上传都认为仍有空间：最终 SQLite 事务只允许符合 10 MiB 总量的文件；见任务 6。
4. 游客绑定 Google 时身份已属于另一个账号：不切换用户 ID、不清除游客刷新 Cookie、不转移文件；见任务 8。
5. 游客升级后旧 Run 恢复：同一 ID 可读旧项目/文件，已用 Token 不清零，异步模型请求使用新的等级默认上限；见任务 2、4、7、8。

---

## 文件结构与接口约定

| 文件 | 职责 |
| --- | --- |
| `new_backend/app/contracts/models.py` | `UserIdentity.is_anonymous`、storage 用量和匿名/升级 DTO |
| `new_backend/app/adapters/live/supabase_auth.py` | Supabase 匿名注册、用户校验、邮箱更新、Google 手动关联 |
| `new_backend/app/domain/identity_policy.py` | 持久账户等级、最后活动、会员/游客配额默认值查询 |
| `new_backend/app/domain/guest_rate_limit.py` | 按可信客户端地址的 HMAC 短期限速 |
| `new_backend/app/api/guest_auth.py` | 游客创建、邮箱升级、Google 关联的 Python HTTP 流程 |
| `new_backend/app/domain/guest_capabilities.py` | 公开 API 与 Pi 内部共用的能力准入判断 |
| `new_backend/app/domain/persistent_conversation.py`、`quota.py` | 按等级取默认 Token/GPU 限额和并发准入 |
| `new_backend/app/domain/catalog.py` | 原子文件总量检查与插入 |
| `new_frontend/src/api/{http,types}.ts`、`src/app/App.tsx`、`src/features/auth/LoginPage.tsx` | 游客 API、恢复和升级 UI |
| `new_frontend/src/features/mono/{MonoWorkspace,UsageSettings}.tsx`、`src/i18n/*` | 游客标记、额度提示、双语文案 |

`IdentityPolicyStore.observe_verified_user(user_id: str, is_anonymous: bool) -> None` 同步服务端身份；`tier_for(user_id: str) -> Literal["guest", "member"]` 为后台任务提供持久判断。`GuestCapabilityPolicy.require_member(user_id: str, capability: str) -> None` 在游客调用需登录能力时抛 `LoginRequired`；`tool_allowed(user_id: str, name: str) -> bool` 过滤 MCP。`CatalogStore` 的三个写入方法增加 `max_total_bytes: int | None`，由 API 按等级传入。接口名字在任务间保持一致。

### Task 1: 身份字段与 Supabase 匿名适配器

**Files:** `new_backend/app/contracts/models.py`、`new_backend/app/adapters/live/supabase_auth.py`、`new_backend/app/domain/store.py`、`new_backend/app/adapters/mock/auth.py`；测试 `new_backend/tests/test_live_adapters.py`、`test_auth_usage.py`。

**Interfaces:** `SupabaseIdentityAdapter.sign_in_anonymously(captcha_token: str | None) -> SupabaseSession`；`verify()` 返回带 `is_anonymous` 的 `UserIdentity`；`DemoStore.issue_anonymous_token() -> tuple[str, UserIdentity]`。

- [ ] 写失败测试：Supabase fake 收到 `POST /auth/v1/signup` 与 `gotrue_meta_security.captcha_token`；返回用户 `is_anonymous=true`；错误/缺失标记按游客权限处理。mock 游客与演示邮箱身份不同。
- [ ] 执行 `cd new_backend && python -m pytest -q tests/test_live_adapters.py tests/test_auth_usage.py`，确认新增断言先失败。
- [ ] 最小实现：复用现有 Supabase 会话解析与异常映射；`UserIdentity` 增加兼容默认值，live 验证对不确定标记保守处理。
- [ ] 重跑同一命令，确认新增与既有身份测试通过；只提交本任务文件。

### Task 2: 持久身份等级与迁移

**Files:** 新建 `new_backend/app/domain/identity_policy.py`；修改 `new_backend/app/db/migrations.py`、`new_backend/app/main.py`、`new_backend/app/api/auth.py`；测试 `new_backend/tests/test_identity_policy.py`、`test_component_migrations.py`。

**Interfaces:** `observe_verified_user()`、`tier_for()`、`last_seen_for()`；未知 ID 在后台准入时按游客权限处理。历史数据库中已有所有者按旧系统的已认证用户回填会员，不重写业务数据。

- [ ] 写失败测试：新游客等级重启后存在；只有 Supabase 验证为非匿名且相同 ID 才升会员；跨实例可见；历史用户迁移后仍可恢复旧 Run；状态冲突拒绝提权。
- [ ] 执行 `cd new_backend && python -m pytest -q tests/test_identity_policy.py tests/test_component_migrations.py`，确认红。
- [ ] 新增 SQLite 迁移、等级观察与 `last_seen`；在 `get_current_user` 和登录/刷新回调中调用，创建游客先落库再返回令牌；不接受客户端等级字段。
- [ ] 重跑同一命令，确认绿；只提交本任务文件。

### Task 3: Python 游客端点、限速与 CAPTCHA

**Files:** 新建 `new_backend/app/domain/guest_rate_limit.py`、`new_backend/app/api/guest_auth.py`；修改 `new_backend/app/config.py`、`new_backend/app/main.py`；测试 `new_backend/tests/test_anonymous_auth.py`。

**Interfaces:** `POST /api/v1/auth/anonymous {captcha_token?: string} -> AuthSessionResponse`；`GuestRateLimiter.claim(client_address: str) -> bool`。live 环境启用 CAPTCHA 时令牌必填，mock 环境使用 Python 假身份适配器。

- [ ] 写失败 HTTP 测试：点击后才创建；重复点击或刷新得到同一游客 ID；同 IP 10 次/小时上限在两个 app 实例同时请求时保持原子；伪造 `X-Forwarded-For` 不换额度；缺 CAPTCHA、Supabase 429/503 不发新会话；已有会员会话不被覆盖。
- [ ] 执行 `cd new_backend && python -m pytest -q tests/test_anonymous_auth.py`，确认红。
- [ ] 实现 HMAC 限速键、24 小时清理、可信 ASGI 地址读取、Supabase 透传和现有 HttpOnly 刷新 Cookie；配置项目级 `anonymous_enabled`、游客限速、CAPTCHA 开关与 `RESEARCH_AGENT_ANON_RATE_SECRET`。Turnstile 站点密钥只配置在前端，验证密钥保留在 Supabase；生产无安全配置时拒绝启用游客。
- [ ] 重跑同一命令，确认绿；只提交本任务文件。

### Task 4: 等级默认额度与 Pi 异步结算

**Files:** `new_backend/app/domain/persistent_conversation.py`、`new_backend/app/domain/quota.py`、`new_backend/app/config.py`、`new_backend/app/main.py`、`new_backend/app/api/usage.py`；测试 `new_backend/tests/test_guest_quota.py`、`test_atomic_quota.py`、`test_pi_gateway_contract.py`、`test_queued_run_recovery.py`。

**Interfaces:** 同一 `tier_for(user_id)` 供 `/usage`、消息准入、模型代理预留、GPU 预留读取；管理员单用户覆盖值优先于等级默认值。

- [ ] 写失败测试：游客 `/usage` 为 20,000 Token/0 GPU；并发消息与模型请求总预留不超过游客额度；会员默认及管理员覆盖保持既有行为；升级后用量不清零；旧 Run 重启后按当前等级继续扣额。
- [ ] 执行 `cd new_backend && python -m pytest -q tests/test_guest_quota.py tests/test_atomic_quota.py tests/test_pi_gateway_contract.py tests/test_queued_run_recovery.py`，确认新增用例红。
- [ ] 把散落的 `1_000_000`/`60` 默认值改为持久策略查询；Pi 并发准入在既有全局/用户上限外再取游客上限 1；保留 SQLite 原子事务。
- [ ] 重跑同一命令，确认绿；只提交本任务文件。

### Task 5: GPU/AF3 与 MCP 多入口准入

**Files:** 新建 `new_backend/app/domain/guest_capabilities.py`；修改 `new_backend/app/api/capabilities.py`、`api/internal.py`、`api/runs.py`、`api/workspace.py`、`app/domain/catalog.py`、`app/services/agent.py`；测试 `new_backend/tests/test_guest_capabilities.py`。

**Interfaces:** `require_member()` 统一抛 `LoginRequired`；游客 MCP 允许列表默认空，只有明确配置的低成本无副作用工具可进入目录、直接调用和 Pi 上下文。

- [ ] 写失败测试：直接 AF3、Pi 内部 AF3、审批与恢复均返回 `LOGIN_REQUIRED` 且无任务/预留；游客看不到未允许 MCP，伪造直接调用/旧 Pi `allowed_tools` 也不能执行；会员路径仍成功。
- [ ] 执行 `cd new_backend && python -m pytest -q tests/test_guest_capabilities.py`，确认红。
- [ ] 增加共享能力策略，HTTP 边界映射 403；同时检查内部提交/恢复与工具白名单，不依赖前端隐藏按钮。未知 MCP 默认拒绝游客。
- [ ] 重跑同一命令，确认绿；只提交本任务文件。

### Task 6: 游客上传总量

**Files:** `new_backend/app/domain/catalog.py`、`new_backend/app/api/catalog.py`、`new_backend/app/contracts/models.py`、`new_backend/app/api/usage.py`；测试 `new_backend/tests/test_guest_uploads.py`、`test_file_lifecycle.py`。

**Interfaces:** `add_file/add_uploaded_file/add_parsed_pdf(..., max_total_bytes: int | None)`；游客单文件 2 MiB、总保存 10 MiB；删除释放总量。

- [ ] 写失败测试：JSON 和流式上传均受限制；并发两个 app 实例上传不会越过总量；超额文件不入库；删除后释放占用；PDF 失败不计额；`/usage.storage` 与真实存储总量一致；会员保留原文件上限。
- [ ] 执行 `cd new_backend && python -m pytest -q tests/test_guest_uploads.py tests/test_file_lifecycle.py`，确认红。
- [ ] 在 CatalogStore 的同一 SQLite `BEGIN IMMEDIATE` 事务中执行 `SUM(size)`、限额检查和插入；mock store 保持相同契约；流式请求在解析前尽早拒绝明显超限。
- [ ] 重跑同一命令，确认绿；只提交本任务文件。

### Task 7: 游客绑定新邮箱

**Files:** `new_backend/app/adapters/live/supabase_auth.py`、`new_backend/app/api/guest_auth.py`、`new_backend/app/contracts/models.py`；测试 `new_backend/tests/test_guest_email_upgrade.py`。

**Interfaces:** `POST /auth/upgrade/email` 发送新邮箱验证；`POST /auth/upgrade/email/verify` 验证后返回同一 ID 的会员会话；沿用 `POST /auth/password/update` 设置密码。

- [ ] 写失败测试：未验证前仍是游客；验证后 Supabase 用户 ID 未变且可读旧项目/文件；错误验证码、邮箱已占用和跨账号令牌不改变原会话；升级后当月已用 Token 不清零。
- [ ] 执行 `cd new_backend && python -m pytest -q tests/test_guest_email_upgrade.py`，确认红。
- [ ] 通过 Supabase 当前用户邮箱更新及 `email_change` OTP 验证 API 实现升级，后端再次取 `/auth/v1/user` 确认非匿名和同 ID 后同步等级；错误分支保留刷新 Cookie。文档列出邮件验证码模板配置。
- [ ] 重跑同一命令，确认绿；只提交本任务文件。

### Task 8: 游客绑定新 Google 身份

**Files:** `new_backend/app/adapters/live/supabase_auth.py`、`new_backend/app/api/guest_auth.py`、`new_backend/app/api/auth.py`、`new_backend/app/db/migrations.py`；测试 `new_backend/tests/test_guest_google_upgrade.py`。

**Interfaces:** `POST /auth/upgrade/google/start` 需游客 Bearer，返回手动关联跳转 URL；一次性 state/PKCE 存游客 ID；回调验证同 ID 后沿用当前刷新 Cookie 与前端 `/auth/callback`。

- [ ] 写失败测试：没有游客 Bearer 或重放 state 均拒绝；并发/跨实例回调只能消费一次；Google 已绑定其他账号时游客文件和 Cookie 保持；正常关联后 `is_anonymous=false` 且旧项目仍可见。
- [ ] 执行 `cd new_backend && python -m pytest -q tests/test_guest_google_upgrade.py`，确认红。
- [ ] 调用 Supabase 手动身份关联而不是普通 `/authorize` 登录；扩展现有持久 OAuth flow 记录 guest ID；callback 同 ID 核验后才置会员，冲突返回可翻译的稳定错误。
- [ ] 重跑同一命令，确认绿；只提交本任务文件。

### Task 9: React 游客入口、保留草稿与双语升级

**Files:** `new_frontend/src/api/http.ts`、`src/api/types.ts`、`src/app/App.tsx`、`src/features/auth/LoginPage.tsx`、`src/features/mono/MonoWorkspace.tsx`、`src/features/mono/UsageSettings.tsx`、`src/i18n/translations.ts`、`src/i18n/errors.ts`；测试 `new_frontend/src/features/auth/LoginPage.live.test.tsx`、`src/features/mono/MonoWorkspace.test.tsx`、新增 `src/features/auth/GuestUpgrade.test.tsx`。

**Interfaces:** `ResearchApi.startAnonymous(captchaToken?: string)`、`beginGuestEmailUpgrade(email)`、`verifyGuestEmailUpgrade(email, code)`、`beginGuestGoogleUpgrade()`；登录/恢复仍由 Python API 提供会话。

- [ ] 写失败前端测试：游客入口只在点击后调用 API；刷新保留同一游客；额度耗尽保留 composer 草稿并显示升级；GPU/AF3 按钮只作提示；游客退出提示不可恢复；中英两种文案和已有账号登录警告可见。
- [ ] 执行 `cd new_frontend && npm run test -- --run src/features/auth/LoginPage.live.test.tsx src/features/auth/GuestUpgrade.test.tsx src/features/mono/MonoWorkspace.test.tsx`，确认红。
- [ ] 通过 `src/api/` 实现端点、会话转换和升级；双语界面使用现有 i18n；访问令牌只存 live 模式内存，不在组件内直接 `fetch` 或调用 Supabase。
- [ ] 重跑目标用例，执行 `npm run typecheck && npm run lint && npm run build`，确认绿；只提交本任务文件。

### Task 10: 匿名账号生命周期与契约交付

**Files:** 新建 `new_backend/app/services/guest_cleanup.py`、`new_backend/scripts/cleanup_guests.py`；修改 `new_backend/README.md`、`new_backend/.env.example`、`contracts/openapi.json`、`contracts/README.md`、前端生成类型；测试 `new_backend/tests/test_guest_cleanup.py`。

**Interfaces:** `GuestCleanupService.collect_candidates(now: datetime) -> list[str]` 仅选最后活动超过 30 天、无活动 Run/任务的游客；`purge_one(user_id: str) -> bool` 先锁定并复查，再用服务端 Supabase 管理凭据确认仍匿名和删除，最后清理本地所有权记录。失败保留可重试记录；默认脚本只 dry-run。

- [ ] 写失败测试：活跃游客/会员/有待处理任务者不入候选；重复运行幂等；缺管理凭据不删除；Supabase 删除失败后可安全重试；共享 SQLite 多实例不能同时领取同一游客。
- [ ] 执行 `cd new_backend && python -m pytest -q tests/test_guest_cleanup.py`，确认红。
- [ ] 实现保守的领取/重查/删除及审计；本地 `deleting` 状态拒绝新 Run 和升级，Supabase 删除成功但本地清理失败时重试，管理员接口的 404 视作已删除。文档说明不可找回、CAPTCHA/可信代理/Supabase 手动关联配置和真实服务联调步骤。
- [ ] 生成 `contracts/openapi.json` 与 TypeScript 客户端类型；执行后端完整测试，以及前端 `npm run test`、`typecheck`、`lint`、`build`；记录外部服务未部署导致的真实联调边界。
- [ ] 只提交本任务文件和生成契约；更新 `docs/superpowers/plans/2026-10-01-backend-completion-checklist.md` 对应验收状态。
