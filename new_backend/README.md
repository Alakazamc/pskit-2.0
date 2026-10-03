# Research Agent 后端

独立 Python FastAPI 服务。默认启用进程内 mock：项目、会话、Token 配额、每日 GPU 配额、MCP 和 AF3 都无需外部服务。Skill 由 `skills/*/manifest.json` 与 `SKILL.md` 提供；资源目录来自当前 MCP 提供者。

**数据库迁移状态（2026-10-03）：** `RESEARCH_AGENT_MODE=live` 必须设置 `RESEARCH_AGENT_DATABASE_URL`，所有新后端业务表位于共享 Supabase PostgreSQL 的私有 `pskit` schema；Web 进程只检查迁移版本，不自动迁移。`GET /health/ready` 在数据库不可用或版本不符时返回 503。下面涉及 SQLite 路径、备份和 `PRAGMA` 的说明只适用于旧部署与离线迁移工具；正式切换见 `deploy/agent/SINGLE_POSTGRES_CUTOVER.md`（迁移计划第 10 步生成）。

## 代码目录

- `app/api/`：公开 HTTP、SSE 和受保护的内部路由；入参和响应类型在 `app/contracts/`。
- `app/services/`：Agent 调度、依赖探测、游客清理和文件处理。
- `app/adapters/mock/`、`app/adapters/live/`：相同端口的替身与外部服务适配。
- `app/domain/persistent_conversation/`：`store.py` 持有 SQLite 连接与事务；`workspace.py` 管项目和会话，`runs.py` 管消息、事件和 Pi Run，`af3.py` 管审批、计算租约和产物，`usage.py` 管 Token/GPU 记账，`recovery.py` 管后台唤醒与中断恢复。公开导入仍为 `app.domain.persistent_conversation.PersistentConversationStore`。
- `app/db/migrations/`：`core.py` 管核心版本 1–14，`components.py` 管各组件版本，`common.py` 管共享迁移事务和补列；调用方仍从 `app.db.migrations` 导入。
- `app/ports/`：身份、Agent 和能力提供者的接口；`app/workers/`：独立后台执行入口。
- `scripts/check_docstrings.py`：列出缺少函数 docstring 的模块；`--check` 可用于完成后的门禁。

## 启动

```bash
cd new_backend
python -m pip install -e ".[dev]"
python -m uvicorn app.main:app --host 127.0.0.1 --port 18080
```

OpenAPI 位于 http://127.0.0.1:18080/openapi.json。前端 Vite 默认代理到这个端口；旧系统目录和端口不受影响。
`GET /health/live` 只检查进程存活；`GET /health/ready` 检查本进程打开的 SQLite 连接，并返回已选择的身份、Agent、MCP 和 AF3 模式。就绪检查保持轻量，不因可选外部服务故障而下线整个 API。配置管理员密钥后，`GET /api/v1/admin/dependencies` 配合 `X-Admin-Key` 可按需主动探测 Supabase Auth 健康端点和模型网关 `/v1/models`，每项最多等待 3 秒；返回可用、不可达、鉴权失败或限流等状态，不返回 URL、密钥或上游错误正文。MCP 状态来自后台周期发现的最近一次结果，多个服务分别显示；首次发现前或最近结果超过两个刷新周期（最少 10 秒）时为 `unknown`。AF3 callback 是计算 Worker 主动领取任务的入站模式，因此显示 `inbound_only`，不代表 GPU 节点在线。未配置管理员密钥时诊断接口返回 404。
每个 HTTP 响应带随机 `X-Request-ID`；`pskit.requests` logger 输出 JSON 方法、路由模板、状态码和耗时，不记录 URL 查询、请求正文或认证头。配置 `RESEARCH_AGENT_ADMIN_API_KEY` 后，受信任管理员可用 `X-Admin-Key` 读取 `/internal/metrics` 的进程内路由计数与耗时总和；未配置或密钥不符时返回 404。指标在进程重启后归零；SSE 耗时覆盖整个连接直到关闭，因此不应与普通请求延迟直接比较。
Pi 持久库用 SQLite `PRAGMA user_version` 标记当前核心 schema 版本为 14。`app/db/migrations/core.py` 明确列出版本 1–14 的升级步骤，在 `BEGIN IMMEDIATE` 事务中执行建表、补列、写入 `core_schema_migrations` 历史及版本号；中途失败会整体回滚，重启后可重试。未标记旧库和版本 1–13 可升级；比程序更新的数据库在建表前拒绝打开。OAuth、目录、工具运行和 MCP 入场租约表使用 `app/db/migrations/components.py` 的组件级迁移记录 `component_schema_migrations`，可在共享 SQLite 文件内独立升级；旧表会在首次运行时纳入记录。升级前应先做 SQLite 快照并备份 Pi 会话目录。

## 运行模式

- `RESEARCH_AGENT_MODE=mock`（默认）：`POST /api/v1/auth/demo` 按邮箱颁发进程内演示令牌，其他 mock 路由按用户隔离。
- `RESEARCH_AGENT_MODE=live`：关闭演示登录。浏览器只调用 Python 的 `/api/v1/auth/login`、`/refresh`、`/logout`、`/google/start`、`/signup`、`/verify` 和密码找回/更新接口；Python 向 Supabase Auth 交换 JWT 并验证用户。Refresh Token 放在 HttpOnly Cookie，JWT 保存在浏览器内存。只启用认证时仅需服务端的 `SUPABASE_URL` 和 `SUPABASE_PUBLISHABLE_KEY`。live 启动时验证 Supabase URL；Pi 模式还验证模型网关 URL，拒绝非 HTTP(S)、嵌入凭据或查询参数，允许本机自托管 HTTP 地址。未部署的 MCP 与 AF3 默认关闭。Google OAuth state 存在 `RESEARCH_AGENT_DB_PATH` 的 SQLite 文件中；多个实例必须共享这一数据库。

本机固定版本 Supabase Docker 配置、验证码邮件服务及启动命令见 [`../infra/supabase/PSKIT.md`](../infra/supabase/PSKIT.md)。本地 `new_backend/.env` 当前配置 Supabase 认证、Pi Agent 和 AF3 callback；服务由 `deploy/systemd/pskit-new-backend.service` 加载该文件。手动运行时需使用 `uvicorn --env-file .env`。`new_frontend/.env.local` 对应 `VITE_AUTH_MODE=supabase`。两份文件均被 Git 忽略，真实凭据不会进入仓库。

游客在 mock 模式可通过 `POST /api/v1/auth/anonymous` 创建身份，再以 `POST /api/v1/auth/upgrade/email` 绑定新邮箱、`POST /api/v1/auth/upgrade/email/verify` 输入验证码；mock 验证码固定为 `000000`，仅供接口联调。live 模式的匿名入口默认关闭；启用时须配置 `RESEARCH_AGENT_ANONYMOUS_ENABLED=true`、Supabase 匿名登录、Turnstile CAPTCHA 和共享限速密钥。前端另配公开的 `VITE_TURNSTILE_SITE_KEY`；Turnstile 验证密钥只保存在 Supabase。Python 将前端的一次性 CAPTCHA 令牌提交给 Supabase Auth，浏览器不直接调用 Supabase。邮箱升级由 Python 使用当前游客令牌调用 Supabase 用户邮箱更新及 `email_change` OTP 验证，验证成功后必须确认用户 ID 不变且 `is_anonymous=false` 才更新会员等级。Supabase 项目需启用手动身份关联和邮件确认，并把 Email Change 邮件模板设置为包含可输入的六位验证码；关闭邮件自动确认，否则可能跳过所需的验证码步骤。用户随后可通过 `/api/v1/auth/password/update` 设置密码。本机自托管 Supabase 已完成普通邮箱注册、验证码确认及登录联调；游客邮箱升级仍需启用 CAPTCHA 后单独联调。参见 [Supabase 匿名用户升级说明](https://supabase.com/docs/guides/auth/auth-anonymous)及 [CAPTCHA 配置说明](https://supabase.com/docs/guides/auth/auth-captcha)。

游客绑定 Google 使用 `POST /api/v1/auth/upgrade/google/start`（需游客 Bearer），返回应在浏览器打开的 `url`。Python 携当前游客 JWT 向 Supabase 手动身份关联端点发起请求，保存一次性 state、PKCE verifier 和游客 ID；回调只在返回的 Supabase 用户 ID 与原游客相同、且 `is_anonymous=false` 时升级配额等级并轮换 Refresh Cookie。已绑定其他账号或身份不匹配时回到前端 `/auth/callback?error=GOOGLE_IDENTITY_CONFLICT`，原游客 Cookie 和数据保持不变。mock 模式返回本地模拟回调地址供接口验证；Google OAuth 尚未配置，真实浏览器流程仍需单独联调。Supabase 需启用手动身份关联，允许 `RESEARCH_AGENT_PUBLIC_API_URL` 对应的回调地址，并配置 Google OAuth 提供者；所有 Python 实例须共享 `RESEARCH_AGENT_DB_PATH`，否则一次性 state 无法跨实例消费。参见 [Supabase 手动身份关联说明](https://supabase.com/docs/guides/auth/auth-identity-linking)。

游客清理由独立运维命令执行，默认只预览：`python scripts/cleanup_guests.py`。它只选择最后活动超过 30 天、没有未完成 Run/任务/待审批的游客。确认部署配置后，设置仅服务端可见的 `SUPABASE_SECRET_KEY` 并运行 `python scripts/cleanup_guests.py --execute --limit 100`；必须处于 live 模式，且多个实例必须共用 `RESEARCH_AGENT_DB_PATH`。每个候选在共享 SQLite 中重新领取和检查，随后用 Supabase 管理 API 确认仍为匿名身份，再删除 Supabase 用户、本地项目/消息/文件/额度及 Pi 会话目录。远端删除后本地失败会保留可重试记录；清理中的游客请求返回 `GUEST_ACCOUNT_DELETING`，已删除身份的哈希审计记录阻止旧令牌重新建档。删除不可找回，先备份 SQLite 与 Pi 会话目录。此脚本不会自动运行，生产环境需由运维定期调度；真实 Supabase 管理 API 尚未联调。

Agent 可独立选择运行模式。默认 `RESEARCH_AGENT_RUNTIME=mock` 保留第一阶段的模拟回复，AF3 mock 由 Python 后台自动推进并产生 Run 事件。设置 `RESEARCH_AGENT_RUNTIME=pi` 后，聊天由 Pi RPC 执行，AF3 mock 完成时自动唤醒同一 Pi 会话。该模式需要 Pi CLI 和可用的模型凭据：

```bash
cd new_backend/pi
npm ci --ignore-scripts
cd ..
RESEARCH_AGENT_RUNTIME=pi RESEARCH_AGENT_PI_PROVIDER=deepseek RESEARCH_AGENT_PI_MODEL=deepseek-chat \
  python -m uvicorn app.main:app --host 127.0.0.1 --port 18080
```

模型密钥只放服务端环境变量。mock 身份模式下，Pi 可按 `RESEARCH_AGENT_PI_PROVIDER` 和 `RESEARCH_AGENT_PI_MODEL` 选择现有模型。live 身份 + Pi 模式可使用服务端 `MODEL_GATEWAY_BASE_URL`、`MODEL_GATEWAY_MODEL` 和 `MODEL_GATEWAY_API_KEY` 连接 OpenAI 兼容入口，例如 LiteLLM、New API 或直连提供方；Pi 请求先到 Python 的 `/internal/model/v1/chat/completions`，只持有当前 Run 的临时凭据，真实网关密钥不会传给 Pi 或浏览器。设置 `MODEL_GATEWAY_KIND=litellm` 后，代理按 Run 的可信归属用户填充 LiteLLM `user` 字段和 `x-litellm-end-user-id`，供用户费用归属与预算使用；本机部署、团队虚拟密钥和预算建立步骤见 [`../infra/litellm/README.md`](../infra/litellm/README.md)。旧的 `NEW_API_BASE_URL` 与 `NEW_API_MODEL` 暂时可作为网关地址和模型名的兼容别名，但密钥统一使用 `MODEL_GATEWAY_API_KEY`；`NEW_API_USER_TOKENS_JSON` 非空时启动会拒绝。没有可用密钥时，发消息会返回 `MODEL_NOT_CONFIGURED`，不会先扣额度。若 FastAPI 不在 18080 端口，设置 `RESEARCH_AGENT_INTERNAL_API_URL` 为它可从 Pi 子进程访问的地址。Pi 的安装版本固定在 `pi/package-lock.json`。本机回环网关替身已验证真实 Pi CLI 的 MCP 工具调用、工具结果回传、AF3 提交后停止和同一会话的 `/pskit_resume`。A6000 真实 AF3 计算已在隔离数据库中完成端到端烟测；现有 live 用户账号与 Pi 自动唤醒尚未做真实 AF3 端到端验证。

Pi 子进程提前退出或 RPC 超时时，首次 Run 在没有新工具副作用的前提下由 SQLite 队列退避重试，最多执行三次。`run.retrying` 会提示前端清除上一尝试的流式残留；工具已启动则失败关闭，模型限流耗尽后也不会在 Python 再重放整轮。AF3 完成后的恢复同样最多尝试三次，第三次中断会写入 `PI_RESUME_FAILED` 终态。该重试保证仅适用于共用同一 SQLite 文件的实例。

`RESEARCH_AGENT_MCP_EXECUTOR` 与 `RESEARCH_AGENT_AF3_EXECUTOR` 默认是 `auto`：mock 身份模式使用演示实现；live 身份模式默认关闭未部署的 MCP 和 AF3，并返回 `MCP_NOT_CONFIGURED` / `AF3_NOT_CONFIGURED`。需要在 live 身份下开发契约时，可以显式设为 `mock`。AF3 `callback` 模式提供受密钥保护的任务领取、租约续期、产物上传和结果回调契约。当前本机 live 后端已通过限定 AF3 路由的回环代理和 SSH 反向隧道连接 A6000 上的常驻接收器与 GPU 计算容器；部署状态、验证结果及故障边界见 [`docs/af3-receiver.md`](docs/af3-receiver.md)。
MCP 可显式设置 `RESEARCH_AGENT_MCP_EXECUTOR=remote`、`RESEARCH_AGENT_MCP_URL=https://example.org/mcp` 与 `RESEARCH_AGENT_MCP_ALLOWED_TOOLS_JSON='["search_pdb"]'` 接入一个 Streamable HTTP 服务。启动时按 MCP 协议发现工具并构建 Skill/资源目录；允许列表以外的远端工具不会向用户或 Agent 暴露。可选 `RESEARCH_AGENT_MCP_BEARER_TOKEN`、`RESEARCH_AGENT_MCP_TIMEOUT_SECONDS` 与 `RESEARCH_AGENT_MCP_REFRESH_SECONDS`（默认 30 秒）只在 Python 服务端使用。发现失败时身份和工作区仍可用，MCP 目录返回 `MCP_UPSTREAM_UNAVAILABLE`；后台会重新发现，并更新 Skill/资源目录而不清除已上传文件。调用超时或远端失败返回脱敏的 `MCP_UPSTREAM_FAILED`，不会把上游错误原文发给浏览器。
多服务时使用 `RESEARCH_AGENT_MCP_SERVERS_JSON='[{"id":"pdb","url":"https://pdb.example/mcp","allowed_tools":["lookup"]},{"id":"uniprot","url":"https://uniprot.example/mcp","allowed_tools":["lookup"],"bearer_token_env":"UNIPROT_MCP_TOKEN"}]'`，最多 16 个服务。每个服务独立发现，公开工具名为 `服务ID__工具名`，例如 `pdb__lookup`；调用时还原远端工具名。同名工具不会混淆。一个服务失联会从目录移除其工具，其他健康服务继续可用；所有服务失联才返回 `MCP_UPSTREAM_UNAVAILABLE`。`bearer_token_env` 指向 Python 进程环境变量，指定后缺失或为空会拒绝启动；不要把密钥直接放进 JSON。多服务配置不能与旧单服务 URL 同时使用。当前用本地协议替身验证，尚未对真实服务联调。
MCP 工具列表公开每个允许工具的 JSON Schema；公开调用与 Pi 内部调用都会按该 Schema 校验参数。缺少必填字段、类型错误或多余字段返回 `INVALID_TOOL_ARGUMENTS`，不会生成工具运行记录。远端调用结果超过 1 MiB 会拒绝，避免写入过大的工具记录。本地协议测试使用同版本 MCP SDK 的 Streamable HTTP 服务替身，覆盖发现、Schema 校验、成功调用、失败脱敏、结果大小与允许列表。
远端 MCP 调用共用执行池，`RESEARCH_AGENT_MCP_MAX_CONCURRENT_CALLS` 默认 8，`RESEARCH_AGENT_MCP_QUEUE_TIMEOUT_SECONDS` 默认 10；排队超时返回 `MCP_CAPACITY_EXCEEDED`（HTTP 429），并有中英双语提示。公开接口和 Pi 内部接口共用这个池。Pi 模式下，共用同一 SQLite 文件的 Python 实例还会原子领取 MCP 入场租约，活跃调用续租，崩溃留下的过期租约可回收；所有实例必须配置同一个并发上限，否则启动拒绝。Pi 内部 MCP 请求携带 `tool_call_id`；直接调用可携带 `Idempotency-Key`（1–128 字符），新版前端会提交这个键并在响应丢失后用原参数重试时复用。相同调用完成后重试读取持久结果；参数或工具名变化返回 `MCP_TOOL_CALL_CONFLICT`；执行中、进程中断或上游失败导致结果不明时返回 `MCP_TOOL_CALL_OUTCOME_UNKNOWN`（HTTP 409），不会自动重放外部副作用。入场前的 429 不占用调用 ID，可稍后重试。同库实例共用持久化的内部工具令牌密钥，Pi 请求可由任一实例鉴权。未带幂等键的直接调用仍按独立请求执行；此机制也不提供跨独立数据库或上游服务的严格 exactly-once 保证，多主机部署仍需共享存储和外部调用对账。
用户在消息中选择 MCP 资源时，服务端只把对应工具 ID 纳入本轮可用工具；远端工具的描述不会被拼进 Pi 系统提示。工具自身公布的 Schema/描述仍作为工具定义提供给模型，部署方应只允许可信服务和工具。
管理员可用 `X-Admin-Key` 调用 `PUT /api/v1/admin/users/{user_id}/skills`，请求体为 `{"skill_ids":["structure-review"]}`，设置该用户可见和可用的 Skill；空数组表示全部禁用，未设置表示使用所有当前可用的 Skill。限制同时作用于 Skill 目录、项目 Skill、消息上下文、MCP 资源目录以及直接 MCP 调用；用户正在进行的工具调用也会重新检查当前授权。Pi 模式中的授权写入 SQLite，mock Agent 模式只在本进程内保存。
Pi 模式可用同一个管理员密钥调用 `PUT /api/v1/admin/skills/{skill_id}/versions/{version}` 注册 Skill，请求体为 `{"name":"RNA Review","description":"Review RNA","tools":["search_pdb"],"instructions":"Inspect RNA evidence carefully."}`。ID 必须为小写安全标识，版本从 1 开始并递增；相同版本与内容可重复提交，内容冲突、降版或覆盖内置 Skill 返回 409。名称、说明、指令和工具 ID 均校验，非远端 MCP 模式只接受已知工具；工具暂不可用时 Skill 不出现在用户目录。同一 SQLite 文件中的其他 Python 实例读取目录时即可看到新版本，已入队 Run 仍使用提交时的上下文快照。指令会进入 Pi 的受信任系统上下文，因此此接口只能由受信任管理员调用；用户级授权继续按 Skill ID 生效，升级时新增工具会同时适用于已有获授权用户。

模型请求的临时错误由 Pi 的 Agent 层自动重试，固定最多 3 次、初始退避 2 秒，provider 层重试固定为 0；Python 不会再重试同一个 Pi 模型请求。重试开始会发 `run.retrying` 事件给前端，但上游错误原文不进入公开事件。Pi 每次报告正数 Token 用量的 assistant 消息都会形成一条 `model_attempt` 观察流水，状态为 `completed` 或 `error`；流水本身不再次扣额，Python 在同一事务内将该轮已知用量超出预留的部分写为 `adjustment`，最终结算时退回未用预留。中断恢复后再次调用模型，前一次已上报用量仍计入总额；AF3 唤醒中断时会退回上一轮已知未用的预留。未报告用量的 429 不会形成虚构的 Token 数，未知中断且没有上报用量时仍保留估算预留。本地 OpenAI 兼容网关替身已驱动真实 Pi CLI 验证：临时 429 重试一次后成功，`insufficient_quota` 的 429 不重试；后者向浏览器发送稳定的 `MODEL_GATEWAY_QUOTA_EXHAUSTED`，前端显示双语说明。模型认证失败和重试耗尽的限流也分别映射为 `MODEL_GATEWAY_AUTH_FAILED`、`MODEL_GATEWAY_RATE_LIMITED`；这些分类基于 Pi 的错误文本，仍需真实网关确认各错误格式。Supabase 登录的 429 则由 Python 连同 `Retry-After` 转发，用户可稍后重试，服务端不自动重放登录。

Pi 模式将会话、事件、AF3 任务、文本文件内容和 GPU/Token 用量写入 `RESEARCH_AGENT_DB_PATH` 指向的 SQLite 文件。浏览器关闭或 Python 进程重启后，已入队和等待中的任务可继续执行；AF3 mock 完成后会自动触发分析。Pi 模型请求失败会产生 `run.failed` 事件；后台唤醒最多重试三次。配额入场与 GPU 预留使用 SQLite 原子事务。Pi Run 有实例归属、10 秒 lease 和每秒心跳；第二个 Python 实例不会误接管仍在运行的 Run，过期 lease 会回收。`RESEARCH_AGENT_PI_MAX_ACTIVE_RUNS`（默认 4）和 `RESEARCH_AGENT_PI_MAX_ACTIVE_RUNS_PER_USER`（默认 2）限制活跃 Pi Run 数量；SQLite 原子领取覆盖同库的多个 Python 实例，额外请求保持排队，恢复中的 Run 优先。每次 Pi 调用在独立会话分支写入，成功后与回复、Run 状态在同一事务提交；首次执行和后台恢复若在新工具启动前中断，最多退避重试三次并向前端发重置流式文本事件。已启动新工具却尚未完成提交的轮次，无论是进程中断还是 Pi 返回错误，都会失败关闭并提示检查任务状态，避免自动重放外部副作用。多机共享 SQLite、Pi 会话文件和更强的工具执行 fencing 仍需实现。
消息表保存判别式 `parts_json`，支持文本、文件、工具调用/结果、产物、引用、进度与错误片段；旧记录没有该列时启动迁移并按原文本读取。用户附件显示名由服务端文件记录解析，客户端传来的名称不会成为消息事实。Pi 完成回复时，MCP `tool_result` 从 Python 持久调用记录读取，AF3 `tool_result` 从任务记录读取，均携带原 `tool_call_id`；不把 Pi 事件中的任意工具结果当成服务端事实。MCP 结果会作为所属用户的消息内容显示，应只接入可信 MCP 服务。引用、进度与错误片段的 Pi 自动投影尚未完成。
Pi 的工具生命周期会投影为 `tool.started`、`tool.updated`、`tool.finished`，公开事件只含工具名、调用 ID 和状态，不含原始参数或工具结果。最终回复的工具卡片和产物片段从事件及 AF3 任务状态生成；AF3 后台任务完成后，工具卡片显示完成状态，结果片段保留任务 ID、实际 GPU 分钟和 `simulation` 来源标记。
Pi 可调用 `update_plan` 提交最多 20 个带稳定 ID、标题和状态的计划步骤。内部 `POST /internal/runs/{run_id}/plan` 使用当前 Run 的工具令牌，只在 Run 运行中接受请求；首次提交产生 `plan.created`，之后的完整快照产生 `plan.updated`，相同快照不会重复写入。当前 Mono 聊天页和工作台计划面板都只展示服务端事件，并可通过 SSE 游标补读恢复。AF3 执行器禁用时，Pi 不注册 `submit_af3`；系统提示不预设 MCP 或 AF3 结果一定为演示数据。
Pi 发出的 assistant 消息边界会投影为 `message.start` 和 `message.end`；只公开角色，不公开原始消息或模型内部用量。文本仍由 `message.delta` 传递，前端按游标去重。
会话列表会返回最近一次 Run 的 ID 和状态；前端重新打开会话时据此补读事件，恢复进行中的任务进度。`GET /api/v1/runs/{id}/events` 持续推送 SSE，服务端每次最多读取 128 条积压事件并依次发送；慢连接会在写入时等待，不会一次加载完整历史。加 `follow=false` 可读取一次性快照。`DELETE /api/v1/runs/{id}` 和 `DELETE /api/v1/af3/jobs/{id}` 可取消运行/任务；未领取 AF3 任务释放 GPU 预留，已领取任务等待实际用量对账。

AF3 callback 模式在 Pi 模式下启用：设置 `RESEARCH_AGENT_AF3_EXECUTOR=callback` 与 `RESEARCH_AGENT_COMPUTE_CALLBACK_KEY`。`RESEARCH_AGENT_AF3_MIN_GPU_MEMORY_MB`（mock 身份模式默认 0；live + callback 模式必须显式设置正数）写入任务资源需求；正式接 GPU 节点前需按实际 AF3 环境配置。计算服务携带 `X-Compute-Key` 调用 `POST /internal/compute/af3/jobs/claim`，请求体为 `{ "worker_id": "a6000", "lease_seconds": 60, "max_jobs": 1, "resources": { "capabilities": ["af3"], "gpu_count": 1, "gpu_memory_mb": 49152 } }`；只领取有 `fold_input` 且匹配的排队任务。领取结果包含 `attempt` 与随机 `lease_token`。运行时调用 `POST /internal/compute/af3/jobs/{id}/heartbeat` 续租，并通过 `/progress` 写入单调进度。网络中断或心跳过期不会让任务自动转给另一 Worker，因为 GPU 进程可能仍在执行；原 Worker 恢复后可继续上报。`GET /internal/compute/af3/jobs/owned?worker_id=...` 可找回领取响应丢失的任务。任务达到绝对执行期限后才失败并进入 GPU 用量待对账状态。容器接收器与计算进程的部署说明见 [`docs/af3-receiver.md`](docs/af3-receiver.md)。
callback 模式的未领取任务超过 `RESEARCH_AGENT_AF3_QUEUE_TIMEOUT_SECONDS`（默认 3600 秒）会自动失败，释放 GPU 预留并向 Run 发送 `AF3_COMPUTE_UNAVAILABLE`。已领取任务从首次领取起超过 `RESEARCH_AGENT_AF3_EXECUTION_TIMEOUT_SECONDS`（默认 21600 秒）也会失败，撤销租约、清除未完成产物并发送 `AF3_EXECUTION_TIMEOUT`；超期续租和上传会被拒绝，前端提供中英双语说明。任务取消或超时后不会再唤醒 Agent；已领取任务保留预估 GPU 分钟为 `pending_reconciliation`，只有原 `attempt` 与 `lease_token` 对应的结果回报可以更新实际用量，不改变失败或取消终态。未领取任务取消或超时会立即释放预留。计算服务未回报时，预估用量会占用任务创建当日的额度；跨日对账和真实消耗核验仍需计算服务提供用量数据。
如 Worker 永不回报、旧记录没有可用租约令牌，运维人员可用 `X-Admin-Key` 调用 `PUT /api/v1/admin/af3/jobs/{id}/gpu-usage`，请求体为 `{"actual_gpu_minutes":9,"reason":"verified from GPU telemetry"}`。仅终态任务可调整；每次改动写入独立审计表，同一数值重复提交不重复记账。`GET /api/v1/admin/af3/jobs/{id}/gpu-usage/audit` 可查看变更来源、调整前后分钟数、原因和时间。管理员必须先核对计算服务日志；接口本身不能推断实际 GPU 消耗。修正后迟到的 Worker 回报若与管理员确认值冲突，返回 `GPU_RECONCILIATION_CONFLICT`，不会覆盖已确认数值。
受信任的计算服务向 `POST /internal/af3/jobs/{id}/result` 发送 `{ "status": "completed" | "failed", "actual_gpu_minutes": 12, "attempt": 1, "lease_token": "...", "simulation": false, "artifacts": [...] }`。已领取的任务必须带当前 `attempt` 和 `lease_token`；未领取的旧契约任务仍可直接回调。结果在 SQLite 中原子结算，重复回调不重复扣费或唤醒；失败会结束 Run，已消耗的 GPU 分钟仍计入用量。演示计时器和 mock Worker 标记 `simulation=true`，Pi 恢复上下文会收到这个标记；`false` 仅表示回调没有声明模拟，不是外部预测已验证的证明。A6000 已放置无 GPU 的双容器烟测部署；尚未连接到新后端的可达 API，也未执行真实 AF3 推理。
`POST /api/v1/af3/jobs` 和 Pi 的 `submit_af3` 工具可附带 `fold_input`，采用 [AlphaFold 3 官方 JSON dialect](https://github.com/google-deepmind/alphafold3/blob/main/docs/input.md) 的 `name`、`modelSeeds`、`sequences`、`dialect=alphafold3`、`version=1..4` 等字段。Python 限制输入为 256 KiB、最多 4 个 seed、32 个实体，并拒绝任何 `*Path` 字段，避免把用户路径交给未来计算节点；实际生物学与模板语义要由真实 AF3 Worker 再校验。输入随任务和审批持久保存；受 `X-Compute-Key` 保护的 `GET /internal/compute/af3/jobs/{id}` 可供计算服务读取任务输入。旧调用仍可省略 `fold_input` 进行 mock 流程，但这种任务不能作为真实预测提交。
回调模式下，计算服务可先以原始字节流调用 `PUT /internal/af3/jobs/{job_id}/artifacts/{artifact_id}?name=prediction.cif&kind=structure&attempt=1`，同时传 `X-Compute-Key` 和 `X-Compute-Lease: <lease_token>`；租约令牌不放进 URL。单个产物最多 20 MiB。服务端保存文件、大小和 SHA-256，重复上传相同内容幂等，冲突内容拒绝。完成回调引用对应产物 ID 后，所属用户可从 `GET /api/v1/artifacts/{artifact_id}/download` 下载。`GET /api/v1/artifacts/{artifact_id}/preview` 仅返回不超过 256 KiB 的 UTF-8 文本产物（包括 CIF/PDB、CSV、JSON 等），不支持的格式或编码返回 415，过大返回 413；下载仍可使用。未上传文件的旧 mock 产物仍可列出元数据，但标记 `available=false`，下载与预览返回 404。文件当前存于 SQLite BLOB，大文件和多机部署需迁移到对象存储。
`python scripts/mock_af3_worker.py` 是独立的计算接口替身：它按领取契约生成标有 `simulation=true` 的 JSON 产物，并通过真实 HTTP 接口上传和结算，完全不运行 AlphaFold，也不产出结构预测。默认声明 49152 MiB 虚拟 GPU 显存，可用 `--gpu-memory-mb` 改变替身能力；仅用于 AF3 服务上线前验证任务、产物、配额和回调链路。
公开 `POST /api/v1/af3/jobs` 可携带 `Idempotency-Key`（1–128 字符）。同一用户用相同键与参数重试，会读取原任务的当前状态，不再预留 GPU 或触发第二次提交；同键改参数返回 `AF3_IDEMPOTENCY_CONFLICT`（409）。键按用户隔离，任务取消后也不释放该键。Pi 模式的映射与 GPU 预留在同一 SQLite 事务提交，同库实例间有效；mock 模式仅在进程内有效。未带键的请求仍视为新任务。

Pi 工具提交 AF3 时，预计 GPU 用量达到 `RESEARCH_AGENT_AF3_APPROVAL_THRESHOLD`（默认 30 分钟）会先持久化 `approval.required` 并暂停。用户在前端明确批准或拒绝，前端调用 `POST /api/v1/runs/{run_id}/approvals/{approval_id}`，请求体为 `{ "decision": "approved" | "rejected" }`。批准时才原子预留 GPU 并创建任务；拒绝时结束 Run，不生成用户聊天消息。重复相同决定只返回原结果，其他用户不能操作。Pi 模式的公开 AF3 直接提交接口会拒绝达到阈值的请求，防止绕过审批；低于阈值的开发用提交仍可使用。

公开 `/api/v1/usage` 返回用户 Token 月限额、GPU 每日分钟限额和已保存文件字节数。会员默认每月 100 万 Token、每日 60 GPU 分钟；游客默认每月 2 万 Token、每日 0 GPU 分钟、单文件 2 MiB、总文件 10 MiB，且最多并发 1 个 Agent Run。`RESEARCH_AGENT_USER_TOKEN_LIMITS_JSON` 和管理员单用户额度覆盖优先于等级默认值。New API 自身的计费点数不作为 Token 数展示。GPU mock 在提交 AF3 时预留 20 分钟，完成时按 18 分钟结算。额度时间边界以 API 返回的 `resets_at` 为准。
`GET /api/v1/usage/entries` 返回当前用户最近的 Token 预扣、调整与直接扣用明细，以及 AF3 任务的预估或实际 GPU 分钟；其他用户不能读取。Pi 模式的 Token 明细从新建流水表读取，旧数据库中已有的历史汇总不会自动反填到流水；mock 模式从 Python 内存账本返回本次进程内的真实操作记录，重启后清空。GPU 行反映任务当前状态，取消任务金额为 0。此接口不提供模型网关的货币费用或私有点数。
首次 Pi 执行的 Token 调整记入首次预留的月份；后台任务完成后的自动恢复会在当月重新原子预留 Token，按模型报告用量在该月调整。恢复时额度不足则不调用模型，Run 返回 `TOKEN_QUOTA_EXCEEDED`，计算结果仍保存在任务与产物中。live Pi 的每次模型请求先经过 Python 代理：代理用请求体字节数的两倍估算输入预算、按剩余额度收紧输出 Token 上限，并在 SQLite 原子预留；明确被网关拒绝的请求释放预算，报告用量后按实际 Token 结算。上游不报告用量或连接中断时保留本次预算，待外部对账。估算依赖实际模型的 tokenizer 与网关遵守输出上限；未用真实服务验证前，不将它称为严格的 Token 硬上限。

设置 `RESEARCH_AGENT_ADMIN_API_KEY` 后，受信任的服务端工具可用 `X-Admin-Key` 调用 `PUT /api/v1/admin/users/{user_id}/limits` 设置 `token_monthly_limit` 与 `gpu_daily_minutes`。未配置管理密钥时该端点返回 404；密钥不进入前端。Pi 模式的额度写入 SQLite，重启后保留。发送消息可带 `Idempotency-Key`（最长 128 字符）；同一用户、会话、键和请求内容重试会返回原 Run，不会重复扣 Token 或创建任务；同键不同内容返回 `IDEMPOTENCY_CONFLICT`。

Pi 模式在请求入口按用户文本、选中 Skill 和附件上下文长度预扣 Token；Pi 返回 `message_end.usage.totalTokens` 时按模型报告的实际 Token 数调整用量，缺少报告时保留估算。live 模式的单次请求还经过模型代理预算预留与输出限额；New API 计费点数仍由 New API 自身管理，不与 Token 混算。Pi 的工具扩展位于 `pi/extension.js`：启用的 AF3/MCP 替身按当前 Run 的 Skill/资源许可使用，内部调用再次校验许可。内置文件和 shell 工具及自动发现的扩展与 Skill 均禁用；服务端登记的 Skill 指令作为可信系统上下文交给 Pi。恢复动作使用 Pi 自定义内部消息，不写成用户消息。Google OAuth 回调地址由 `RESEARCH_AGENT_PUBLIC_API_URL` 生成，应在 Supabase 的重定向白名单配置；前端页面地址由 `RESEARCH_AGENT_FRONTEND_URL` 指定。HTTPS 部署应设置 `RESEARCH_AGENT_AUTH_COOKIE_SECURE=true`。注册/找回密码的验证码流程需 Supabase 邮件模板提供 OTP；默认邮件链接尚未做真实浏览器联调。

## 目录

| 目录 | 职责 |
| --- | --- |
| `app/api/` | HTTP 路由、身份依赖、SSE 输出 |
| `app/contracts/` | Pydantic 请求、响应与事件模型 |
| `app/domain/` | 会话、目录、Token/GPU 配额规则 |
| `app/ports/` | 身份、MCP 与 AF3 提供者接口 |
| `app/adapters/mock/` | 可预测的身份、MCP、AF3 演示实现 |
| `app/adapters/live/` | Supabase 身份、Pi RPC 与远端 MCP 适配 |
| `app/services/` | Pi Run 执行与后台任务唤醒 |
| `pi/` | 固定版本的 Pi CLI、系统提示与 AF3 工具扩展 |
| `tests/` | 公共 HTTP、SSE、配额与适配器测试 |

HTTP 与 SSE 事件契约快照在 `../contracts/openapi.json` 和 `../contracts/run-event.schema.json`，更新命令：

```bash
PYTHONPATH=. python scripts/export_openapi.py
```

SQLite 可用 `python scripts/sqlite_maintenance.py backup ./data/agent.sqlite3 ./backups/agent.sqlite3` 在线创建一致快照。恢复需先停止所有 Python 实例和 Pi 进程，再执行 `python scripts/sqlite_maintenance.py restore ./backups/agent.sqlite3 ./data/agent.sqlite3 --force`。Pi 会话文件不在 SQLite 中。需要一起恢复 Agent 上下文时，先停止 Pi 和 Python 写入，再执行 `python scripts/sqlite_maintenance.py backup-bundle ./data/agent.sqlite3 ./data/pi-sessions ./backups/workspace-20261001`；恢复到空目标路径用 `python scripts/sqlite_maintenance.py restore-bundle ./backups/workspace-20261001 ./restored/agent.sqlite3 ./restored/pi-sessions`。Bundle 包含 SQLite 一致快照、Pi 会话文件及 SHA-256 manifest，恢复前验证文件哈希与 SQLite 完整性，默认拒绝覆盖现有数据库或会话目录。会话文件拷贝期间不能有 Pi 写入；脚本无法替正在运行的 Pi 建立原子快照。

验证：

```bash
python -m pytest -q
ruff check .
node --test pi/extension.test.js
```

`PUT /api/v1/files/content?name=...` 接收原始文件流并在读取时限制大小：UTF-8 文本、Markdown、CSV/TSV、JSON、FASTA/FASTQ、PDB/mmCIF、VCF/BED/GFF/GTF、GenBank、SDF/MOL/MOL2 最大 1 MiB，可提取文字的 PDF 最大 10 MiB、100 页。上述科研文本格式目前按 UTF-8 原文保存供 Agent 使用，尚未做结构化格式校验。PDF 原文件用于下载，提取出的文字用于 Agent 上下文；扫描版 PDF 尚不支持 OCR。PDF 由独立 Python 子进程解析，限制 1 GiB 地址空间和 15 秒 CPU 时间，默认 30 秒墙钟期限可用 `RESEARCH_AGENT_PDF_PARSE_TIMEOUT_SECONDS` 调整；超时返回 `FILE_PROCESSING_TIMEOUT` 且不保存文件。解析期间主事件循环可继续处理请求。PDF 子进程由执行池限制并发数（`RESEARCH_AGENT_PDF_MAX_CONCURRENT_PARSES`，默认 2），排队超过 `RESEARCH_AGENT_PDF_QUEUE_TIMEOUT_SECONDS`（默认 5 秒）返回 `FILE_PROCESSING_BUSY`。Pi 模式下，共用同一 SQLite 文件的 Python 实例还会原子领取 PDF 解析租约，取消请求后直到解析工作结束才释放；过期租约可回收，配置不同的实例拒绝启动。多主机独立数据库之间不共享名额；持久解析队列仍未实现。旧的 JSON `POST /api/v1/files` 保留文本上传兼容。`GET /api/v1/files/{id}/download` 与 `DELETE /api/v1/files/{id}` 按用户归属检查。请求中的 Skill、资源和文件 ID 由服务端重新解析，不信任客户端名称。默认 mock 模式的文件与任务状态在重启后清空；Pi 模式使用 SQLite 持久保存文件与任务。A6000 上的真实 AF3 已接入；MCP 服务与多机 GPU 调度仍待接入。模型网关的具体账号与限流行为需按实际服务联调。

内置 Skill `manifest.json` 必须包含与目录名相同的安全 ID、正整数 `version`、名称、描述和已注册的工具 ID；启动时还会检查非空 `SKILL.md`。清单不合法会使服务启动失败。动态 Skill 版本及指令保存在 SQLite，不使用文件路径；它们与内置示例一同通过 `/api/v1/skills` 提供给前端。
