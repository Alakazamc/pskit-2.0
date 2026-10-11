# PSKit 2.0：最新审查后的精确整改执行方案

> **给 Codex 的实施任务书（可直接复制使用）**
> 目标仓库：`Alakazamc/pskit-2.0`
> 目标分支：`codex/new-stack-baseline`
> 本轮审查 HEAD：`c37ca61e7a8d9672013597f2cc339f4e9060720f`（2026-10-11 14:54:16 UTC）
> 对照旧任务书基线：`5d703951d5ed50302e59c1f91e181ada05af81bd`
> GitHub Actions：`https://github.com/Alakazamc/pskit-2.0/actions/runs/38149526876`
> 文件性质：**源码审查后的整改实施计划，不代表已完成或已验证本次修改。**

## A. 当前结论与约束

1. 上一轮任务书已经大量落地。新版源码现位于 `apps/api/`、`apps/web/`，不是 `new_backend/`、`new_frontend/`。`AgentService` 已抽出调度/事件/执行/恢复模块；`ModelRuntimeProfile`、资格测试框架、共享 HTTP Gateway、动态 Compute 业务 Tool、`pskitctl` 已存在。**不得重复大搬家、重复造模块或推倒已有修复。**
2. 当前**不能认定整改完成**，原因是最新远端 `new-stack CI` 失败：`backend` 仅在 Ruff `I001` 被阻断；`frontend` 有 **8 failed / 379 passed**；`pi`、`gateway-protocol`、`contracts` 成功；依赖它们的 `deployment-model-and-image` 被跳过，尚未对本 HEAD 验证。
3. 目前未发现新 OpenAI-compatible 供应商完成真实 Staging Tool Calling → 工具结果续聊 → Workspace Artifact 的证据。当前 PinCC 限定补丁已有独立离线验收，不要改成全局请求补丁。
4. `deploy/agent/` 仍约 **159 文件 / 17 个 Compose YAML**；`deploy/agent/scripts/release_control.py` 约 **1252 行**。统一命令入口已存在，但内部复杂度仍高，不能用再次移动文件冒充收敛。
5. **不允许擅自连生产、SSH、部署、迁移真实数据库、更新线上网关或接收器、清理数据卷、消费付费 API。** 可以做本地源码、Mock、隔离 PG17、离线镜像、合成 Compose 测试。涉及真实供应商 / Staging 要单独等待授权。不可把真实凭据或用户内容写入日志。
6. 不新增 LangGraph、Redis/Celery、Kubernetes、Next.js、额外模型代理微服务；保留 FastAPI + React/Vite + Pi + LiteLLM + PostgreSQL + OpenSandbox + MCP/Compute 既有架构。
7. 一项变更一个独立可审查提交；先 RED 回归，再 GREEN；禁止通过删除失败用例、无条件 skip、提高超时蒙混、关闭功能来制造绿色结果。

## B. 执行顺序总览

| PR | 优先级 | 任务 | 完成 Gate |
|---|---|---|---|
| **PR-0** | P0 | 修最新 Backend Ruff I001 与相关静态检查 | Ruff / compile 全绿；不要触碰业务逻辑 |
| **PR-1** | P0 | 定位并修复前端 8 个失败测试，特别是历史消息取消竞态 | 四个失败文件重跑多次 + 整套 Vitest、typecheck、lint、build 通过 |
| **PR-2** | P1 安全 | 收敛 Tool 进度/失败摘要，杜绝不可信原始输出通过 Run Event 泄露 | 所有摘要仅安全编码/白名单字段；秘密/大输出/控制字符回归通过 |
| **PR-3** | P1 | 模型资格验收与 OpenAI-compatible 接入可靠性完善 | 离线 PinCC/OpenAI/Anthropic 对照 CI 全绿；真实供应商在独立 Staging 授权后验收 |
| **PR-4** | P1 | `pskitctl` 控制器内部拆分、文档与部署矩阵收敛 | 原 CLI/Manifest/准入、故障恢复与回滚行为回归一致；无停库路径 |
| **PR-5** | P2 | Agent 通用 `submit_compute` 渐进下线、长时观察与历史栈管理 | 受控兼容开关、业务 Tool 真实 schema、计算账本/唤醒/授权回归通过 |

**顺序约束：PR-0 和 PR-1 通过远端 GitHub Actions 之后，才能合并继续进行 PR-3/PR-4 的大修改。PR-2 若被判定为潜在敏感信息暴露，可以并行做小型安全补丁，但不可再让主干 CI 变红。**

---

## PR-0：Backend Ruff 导入顺序（P0，立即修）

### 实际失败证据

远端 `backend` job 失败步骤 `Lint, compile, unit and PostgreSQL 17 tests in isolated configuration`，日志报：

```text
I001 [*] Import block is un-sorted or un-formatted
apps/api/app/services/agent_events.py:3:1
```

最新提交 `c37ca61e` 仅增加工具失败诊断及 `import re`，不是已经出现了 Python 单测失败：**Backend pytest 本轮根本没有开始执行。**

### 操作

```bash
cd apps/api
python -m ruff check app/services/agent_events.py --select I --fix
python -m ruff check app tests
python -m compileall -q app tests
```

限定提交范围为导入顺序及必要的格式修复，不将此作为时机重写安全摘要逻辑。然后完整运行 `python scripts/ci_new_stack.py backend ...`（需专用 loopback PostgreSQL 17，遵循 `docs/runbooks/NEW_STACK_CI.md`）；不允许让测试因缺 PG17 静默跳过。

### 验收

- 远端 Ruff 不再报 I001。
- Backend、PostgreSQL 17 单测实际运行，不得被误写为“原先全绿”。
- 其他变更独立 PR/commit。

---

## PR-1：Frontend 8 个实际失败测试（P0，最高优先）

### 精确失败清单

来源：`https://github.com/Alakazamc/pskit-2.0/actions/runs/38149526876` 的 frontend job。

| 文件 | 失败 | 关键断言或场景 |
|---|---:|---|
| `apps/web/src/features/chat/useMessageHistory.test.tsx` | 1 | `does not consume accepted optimistic messages when a latest-page refresh is cancelled`：预期 `['m1','pending-send']`，实际 `['m1']` |
| `apps/web/src/features/mono/AttachmentPreviews.test.tsx` | 2 | 已保存图片草稿刷新后预览恢复；下载晚于移除不应复活附件 |
| `apps/web/src/features/mono/MessageAttachments.test.tsx` | 4 | 同名图片显示、pending 预览、失败重试、切换会话时的晚到请求隔离 |
| `apps/web/src/features/mono/SettingsPage.test.tsx` | 1 | 头像上传后侧栏同步，未出现预期 Blob URL |

### 先复现，后修复

```bash
cd apps/web
npm ci
npm run typecheck
npm run lint
npm run test -- src/features/chat/useMessageHistory.test.tsx
npm run test -- src/features/mono/AttachmentPreviews.test.tsx src/features/mono/MessageAttachments.test.tsx src/features/mono/SettingsPage.test.tsx
npm run test
npm run build
```

在 GitHub runner 尽可能一致的 Node 22、同一 package-lock 下做复现。分别记录独立测试与全套并发测试是否都失败，确定是**业务竞态、测试替身不完整还是 CI 性能压力**。逐项记录：RED、原因、最小补丁、GREEN 命令和时长；不得只把 `waitFor` 超时提高或移除断言。

### 1A. 乐观消息被取消的查询覆盖

相关代码：
- `apps/web/src/features/chat/useMessageHistory.ts`
- `apps/web/src/features/mono/useConversationSend.ts`
- `apps/web/src/features/chat/useMessageHistory.test.tsx`

源码中 `useMessageHistory()` 的 `queryFn` 读取和写入同一个 TanStack Query Cache，同时维护 `acceptedPending`，但在 `cancelQueries()` 时状态可能被撤销/回滚。现象明确：`pending-send` 消失。这需要先按现有 Query 版本复现，不能凭经验盲改。

**推荐设计：**

- 让服务端消息的 Query `queryFn` 尽量纯粹地只负责取回分页数据，副作用由受控更新层处理。
- 乐观消息单独放到与服务器 Query 取消状态解耦的 `optimistic-messages` 层（可以是一个独立的 QueryKey，或者受控 Zustand/State 层；不要再造独立数据持久化系统）。将 `messageId`、`idempotencyKey`、提交状态绑定，不仅保存一个裸 ID。
- 页面展示合并**服务器确认消息 + 当前未确认乐观层**；`cancelQueries`、404 fallback、切换会话不得删掉已经被服务端接受但还未反映在数据页中的乐观消息。
- `POST` 失败时移除对应乐观消息，保留原草稿和幂等键的恢复语义；收到服务端权威确认再替换，避免同内容重复提交。如果服务端现有 response 无稳定关联 ID，先评估使用现有幂等键/返回信息；确实不足时只做兼容性**新增可选字段**，不可重写消息 API。
- 不改变 `['messages',userId,sessionId]` 已有缓存键使用方，不破坏聊天 `SSE`、分页、历史加载。
- 测试：cancel 发生于请求前/后/数据返回前；POST 已接受与尚未接受；再刷新、重新进入会话；前后消息时间戳相同；账号切换；分页重叠/缺口；不得重复、丢失或串会话。

### 1B. 图片草稿与消息历史预览

相关代码：
- `apps/web/src/features/chat/useAttachmentPreview.ts`
- `apps/web/src/features/chat/AttachmentImagePreview.tsx`
- `apps/web/src/features/chat/MessageImageAttachment.tsx`
- `apps/web/src/features/chat/Composer.tsx`
- 对应 `AttachmentPreviews.test.tsx`、`MessageAttachments.test.tsx`

当前 `useAttachmentPreview` 使用 `useQuery` 加 `useLayoutEffect` 创建对象 URL；需要重点核对 `image.data`、`source` 和组件卸载时机之间的一致性。不要未经复现就断言此 Hook 是唯一根因；还要检查历史消息 query 是否成功 fallback 到 `/messages`。

**修复要求：**

- 上传草稿优先用当前 `initialImage`；刷新后按 `{userId,fileId}` 从授权文件 API 恢复；同名文件必须按 ID 区分。
- `Blob URL` 只绑定当前 Blob 和当前挂载，数据替换或卸载时恰好清理；晚到请求不能让已移除的附件或旧会话图片重新出现。不要把 URL 写进 LocalStorage。
- 下载失败要保持文件引用、可重试按钮和安全占位；重试恢复时不能因为 `failedUrl` 保留而屏蔽新 URL。
- 将真实用户可见的图片加载状态（loading/failed/ready）与 `React Query` 的瞬时 `isFetching` 分开验证；可给 Hook 增加有限状态类型，但不添加第二个全局 Blob Cache。
- 测试使用独立 QueryClient 和明确的可控 Promise；检查 jsdom `URL.createObjectURL` Mock、mock Response 的 `.blob()` 与页面真实事件，排查仅在 CI 并发运行时出现的时序竞争。
- 使用 React Testing Library 的 `findBy` / 条件 `waitFor` 等待有意义的 UI 状态，不要固定 `sleep`、也不要全局关掉超时。

### 1C. 用户头像状态联动

相关代码：
- `apps/web/src/components/UserAvatar.tsx`
- `apps/web/src/features/mono/ProfileSettings.tsx`
- `apps/web/src/app/App.tsx`
- `apps/web/src/features/mono/SettingsPage.test.tsx`

链路必须保持：上传成功 → 更新 `UserIdentity.avatar_revision` → `['avatar',userId,revision]` 查询 → Blob → Blob URL → 侧栏展示；删除反向更新且撤销 URL。这里要先排查 CI 失败时 1) `onUserChange` 是否触发，2) 用户身份是否更新，3) GET /me/avatar 是否发出，4) Mock Blob URL 是否生成，5) 是否被异步渲染/组件重装覆盖。**没有定位之前不要粗暴延长等待、强制 refetch 所有查询或放松测试。**

### Gate

- 上述四个失败文件在单独运行和全量运行均通过，建议至少重复 3 次以检测 race。
- Vitest **387/387**（若新增测试则以新总数为准）、`typecheck`、`lint`、`build` 全绿。
- 保持真实的 session/user 文件隔离、图片 owner-check、URL 回收和乐观消息幂等语义。
- 推送后最新 `new-stack CI` 前端绿色；Backend 也应独立通过。

---

## PR-2：工具诊断安全与可信边界（P1）

相关代码：`apps/api/app/services/agent_events.py`（HEAD 最新提交 `c37ca61e`）

现状：`tool_execution_end` 失败时增加 `_tool_failure_summary()`，对原始工具文字进行若干正则脱敏后最多取 500 字符。但同模块 `tool_execution_update` 当前仍可能把 `partialResult.content[*].text` 最后一行直接作为 `ToolUpdatedData.summary` 写入 Run Events；该分支没有同等脱敏。**这是潜在敏感信息/不可信输出进入事件流的风险，不等于已证实泄漏。**

**修改要求：**

1. 定义**唯一**的安全 `ToolProgressSummary` / `ToolFailureSummary` 策略，所有进度与失败摘要走相同审查，不允许任意原始工具输出直接进入公开 SSE / 持久消息/审计摘要。
2. 首选来自服务端可信边界的枚举型状态码及白名单文本；原始错误和长日志只在有权限的诊断通道留存，默认不向普通用户显示原文。若要展示任意文字，必须有明确字段来源、最大长度、控制字符约束、内容敏感级别和结构化审计。
3. 兼容当前已有错误码及 `ToolStarted/Updated/FinishedEvent` 契约，避免更改公开 `RunEvent` 类型。
4. 增加 `Bearer xxx`、`sk-...`、JWT、`Authorization`、查询参数里的 Token、URL 凭据、换行、ANSI/控制字符、超长二进制/日志以及敏感科研输入的负面测试。不能只用正则替换的有限模式来宣称任意秘密都不会泄露。
5. 在做完 PR-0 Ruff 修复后单独提交此处功能改动，保持 review 边界。

**Gate：** `tool.updated` 与 `tool.finished` 的可见摘要均走同一个可复测安全机制；失败仍给用户明确错误类别，不把错误变成成功；SSE、Artifact 和工具结果存储不倒退。

---

## PR-3：LiteLLM / OpenAI-Compatible 接入（P1，先验证再扩展）

### 已经有的基础，不要重复实现

- `apps/api/app/contracts/model_runtime.py`：`ModelRuntimeProfile`, `agent_ready`, 资格证据。
- `apps/api/app/domain/admin/model_policy.py`：模型发布与用户授权。
- `apps/api/app/domain/admin/model_qualification.py`、`services/model_qualification_driver.py`：资格检查与受控执行。
- `apps/api/app/adapters/live/pi_model_config.py`：Pi Config Builder。
- `apps/api/app/services/model_gateway.py`：共享 `httpx.AsyncClient` 和结构化错误。
- `infra/litellm/provider_compat.py`：仅 PinCC 原生 Anthropic 特例，严格绑定 LiteLLM `1.100.3`。
- `.github/workflows/new-stack.yml` 的 `gateway-protocol`：当前远端已经通过。

**不要再增加另一套 Provider Registry、Auth Policy、Token Ledger 或另一个代理微服务。**

### 完善兼容验收矩阵

为每个独立 OpenAI-compatible alias + 真实上游路由版本/部署指纹验证：

| 测试 | 验收 |
|---|---|
| `/v1/models` | 可正确发现别名；没有 `/model/info` 时不推断未证实能力 |
| 文本非流式 | `choices[0].message.content` 正确 |
| SSE 文本 | 增量、`[DONE]`、异常中断和最终状态正确 |
| Tool Call | 真的返回 `tool_calls[].function.name/arguments` 而非 `<tool>` 伪文本 |
| Tool Arguments Streaming | JSON 参数跨 delta 正确拼接，工具 ID 和 index 可靠 |
| Tool Result 续聊 | `role=tool` 和 `tool_call_id` 被接受且模型正常继续 |
| `tool_choice` | `auto` / `required` / 强制特定工具是否支持，按真实能力记录 |
| Usage | 有报告则按可信字段结算，缺失/超时保留 unknown 预占，不按零花费 |
| 错误 | 401/402/403/429/5xx/timeout 与 Retry-After 稳定映射，密钥脱敏 |
| Reasoning | 不同 effort 的请求是否被支持，`off` 到 `none` 映射是否正确，禁止未证实能力展示 |
| Vision | 输入图片 MIME/数量/大小限制，真实模型识别成功后才发布支持能力 |
| 完整 Agent | 真实 Pi 调用 Workspace File Tool，`write_file → read_file → artifact.created → 鉴权下载` |
| 路由隔离 | PinCC 特例不影响官方 Anthropic/普通 OpenAI-compatible，混合能力部署不误声明 Agent Ready |

**发布门禁：** `offline` mock 只能证明本地协议逻辑；真实新供应商的 `agent_ready` 仍须在**另行授权的独立 Staging 环境**完成、记录模型/路由指纹、请求费用、真实工具事件、Artifact 与回退。未提供供应商凭据时保留 `not_tested`，不将其视为“已通过”。

### 特别事项

- 不全局移除 `tools[*].type`；只保留已限定的 PinCC 修复，并在升级 LiteLLM 前固定 wire 回归。
- 不依赖 `drop_params=true` 隐式吞掉不支持的 reasoning/tool 参数来判定模型可用。
- 此次先支持 OpenAI-compatible **Chat Completions**；如果只有 Responses API，保持明确 `MODEL_PROTOCOL_UNSUPPORTED`，不要未经真实上游需求就实现未经验证的协议转换。
- 当前 `ModelQualificationDriver` 约 639 行：在行为验收之后可按请求生命周期拆成 `probe/transport/ledger/artifact` 内部组件，保留已有外部接口和账本策略；切勿为了文件行数改动已有安全锁。

---

## PR-4：发布控制和部署目录复杂度收敛（P1）

### 现状

- 当前正式源码在 `apps/api`、`apps/web`；旧栈仍是 `backend/`、`frontend/`，不需要再次迁移新栈。
- `deploy_to_cloud.sh` 已被禁用为 fail closed（所有参数返回 exit 2）；不能恢复它旧的全栈 `down/up` 行为。
- `deploy/agent/pskitctl` 是当前日常 app-only 运维入口；控制器约 1252 行；Compose 17 份仍有现行、迁移和回退不同用途。
- `release_control` 已有可选、独立准入组件与不可变 Manifest、原子 Web 切换以及 receiver 观察；本地故障测试已存在。尚未完成受控宿主真实采用和 Staging 回滚演练。

### 先保证不变量，不要先整理文件名

- 普通 Web/Backend 发布绝不因自身升级而执行 Supabase/LiteLLM `down`、数据库 provision/migration 或删除卷。
- API 更新前必须经过本宿主已资格验收的 durable admission barrier（包括活跃 Run/Job/Attempt/Receiver 状态、unknown/待结算、lease/journal/outbox）；未证实不得打开准入、更不得报告成功。
- 用户输入、provider key、私有 DSN、Token 不进入 Manifest/日志；当前与回退镜像、Compose 顺序和 source bind 必须固定。
- 旧 hashed Web assets 在切换/回滚期间继续可访问；失败可恢复，不擅自回退 DB schema。
- 未完成云端 source bind、host adoption、真实 Staging/RPO-RTO 演练之前，继续 fail closed，不将本地 mock 视作生产资格。

### 把 1252 行 controller 内部拆成可单测模块

建议**仅在 `deploy/agent/scripts/` 内机械性拆分**，保留 `deploy/agent/pskitctl` 外部 CLI/参数及完整行为：

```text
deploy/agent/
├── pskitctl
├── scripts/
│   ├── release_control.py      # 薄 CLI / Controller facade
│   ├── release_manifest.py     # 当前不可变 Manifest 验证（保留）
│   ├── release_files.py        # 当前文件与原子操作（保留）
│   ├── release_config.py       # 配置/Compose 组合与安全只读验证
│   ├── release_admission.py    # durable barrier 与资格回执编排
│   ├── release_activity.py     # DB/Receiver/journal/outbox 只读探针
│   ├── release_api.py          # API-only 更新和恢复
│   └── release_web.py          # Web-only 原子发布和恢复
└── README.md                    # 唯一当下入口索引
```

这是职责建议，不是要求创建所有文件。每一轮只迁移一个私有职责：先抽不含 I/O 的验证纯函数，再抽带回退状态的组件；尽量沿用已有 `release_files.py` 等模块。**准入 enter/verify/release、owner/nonce、fencing、出错后的保守闭锁绝对不允许跨模块拆开后出现状态歧义。**

### Compose 分类而不是拍脑袋删

为 17 个 `compose*.yaml` 生成 `DEPLOY_MATRIX`，每份标记：`active-prod / active-staging / active-local / bootstrap-only / migration-only / rollback-only / obsolete-candidate`；注明当前依赖机器、项目、overlay 顺序、环境变量来源、Source bind、卷、网络、回滚入口。现行 Compose 保持原路径直到真实宿主引用审查、`docker compose config` 等价和 Staging 回滚演练通过。

后续若想把历史文件移入 `deploy/archive`，单独纯迁移 PR；不要把不同 Compose 项目（Supabase、LiteLLM、Agent、OpenSandbox）强行合并成一个项目。**目标是让操作员只认一个 CLI 和一份当前部署矩阵，而不是追求 YAML 数量尽可能少。**

### 过时文档与目录

- 更新 `docs/architecture/COMPLETION_REPORT.md` 的顶部**当前状态**：`46c1dc4` 后 `apps/*` 已完成本地纯目录迁移。历史报告应保留当时未迁移的原始事实，采用清晰的“当时状态/当前补充”分隔，不能直接篡改原始证据。
- `README.md` 已使用 `apps/*`，应继续保持；明确 `new_*` 不存在且不是推荐入口。
- 旧 `backend/`、`frontend/` 和根 `ci.yml` 仍有历史回滚/旧栈意义。在 source bind、回滚、独立测试全部核对之前**不删除、不盲目移入 legacy/**。

### Gate

- `pskitctl` 命令及外部 Manifest 原样工作；关键入库闭锁、unknown、取消、回滚状态机回归全部 green。
- 合成 Compose 9 类测试通过，PR 源码构建的镜像检查（非 root、依赖固定、构建 context）通过。
- 在另外获授权的 Staging，分别演练 Web-only、API-only、拒绝不合格 release、断电/中断恢复、活跃长任务期间 fail-closed 与回滚；不得从本地 mock 测试推出上线成功。

---

## PR-5：保留兼容但降低 Agent 工具表面复杂度（P2）

动态业务 Tool 已在 `apps/api/pi/extension.js` 第 302–320 行实现，`compute_tools.py` 在服务端绑定发布 release/version/budget，方向正确。但第 322–345 行仍把泛化 `submit_compute(capability_id, version, arguments, budget)` 暴露给 Pi。

**建议渐进迁移：**

1. 保留现有内部 `/internal/compute/jobs` 端点及老调用的实际恢复能力，避免历史 Pi transcript/未完成 Run 无法续跑。
2. 对**新创建**、已在模型资格框架允许的 Agent Run，默认仅暴露发布业务 Tool；旧通用 Tool 通过显式兼容开关/历史 Run profile 可见。
3. 后端强制根据不可变 release 和真实用户 policy 决定 version、预算、授权和资源预占；LLM 不得自定义或绕过预算。
4. 兼容式回归覆盖老 Run resume、AF3、Tool Product、同一个 tool_call_id 的幂等执行、Terminal usage、Cancel、Receiver contract。
5. 仅当有真实历史使用量和灰度结果，再决定最终删除 `submit_compute` Pi tool；不要贸然改写现存会话的 transcript。

---

## C. CI/测试执行与最终交付

### 测试门禁的真实依赖顺序

先将 PR-0/PR-1 推送到**当前分支**，确认对应 HEAD 的 GitHub `new-stack CI` 全绿，不能引用旧 HEAD 的 green 结果。当前工作流真实 jobs：

```text
backend (Python 3.12 + PG17 + Ruff + pytest)
frontend (React typecheck + lint + Vitest + build)
pi
contracts
gateway-protocol (pinned LiteLLM)
          ↓
deployment-model-and-image (Compose + release tests + current image)
```

如果 Backend 因 Ruff 失败、Frontend 因测试失败，下游 deployment/image 为 skipped，应视为**未验证**而不是成功。

完整本地入口（使用单独的 loopback PG17，配置请读仓库已有手册）：

```bash
python scripts/ci_new_stack.py backend --timeout 1800 --report-dir /tmp/pskit-backend-results
python scripts/ci_new_stack.py frontend --timeout 720
python scripts/ci_new_stack.py pi --timeout 300
python scripts/check_new_stack_contracts.py
python scripts/ci_render_compose.py
python scripts/ci_new_stack.py deployment --timeout 600
```

CI 出现故障后，应保留完整 stdout/stderr、JUnit、失败用例、固定环境版本和最终成功的 commit SHA，不能仅给出“已全部修复”的口头总结。

### 向我交付的结果格式

每个 PR/阶段给出以下表格，必要时附最小复现步骤与回滚手册：

| 字段 | 必填内容 |
|---|---|
| 基线 | 修改前 HEAD / 修改后 SHA / changed files |
| 事实依据 | 对应 GitHub CI job / 源码行 / 可复现的 RED 用例 |
| 根因 | 具体生命周期/Query/协议/部署不变量，不要只写“竞态” |
| 方案选择 | 最小补丁 vs 其他方案；为什么不引入额外复杂度 |
| 回归 | 本地测试命令、passed/failed/skipped、真实 PG/Mock/浏览器区分 |
| 远端结果 | 新 HEAD 对应 Actions URL + 每个 job 的 conclusion |
| 风险和回滚 | 已知未测边界、兼容旧版和生产回滚方式 |
| 下一阶段 | 仍待 Staging/Host/真实模型资格，不能写成完成 |

### 最后强制边界

- 本轮优先目标不是再写新功能，而是**恢复远端 CI green、修真实竞态、消除错误摘要的不可信文本路径，并为模型/部署完成可验证 Gate**。
- 没有真实供应商测试账号，不模拟出 `Agent Ready` 资格；没有宿主 adoption，不开启正式 API release。
- 目录已经迁到 `apps/api`、`apps/web`，不要再次搬运；旧目录保留在根的历史栈，与新栈不是同一套代码。
- 在 CI 修绿前，不对生产进行任何修改或发布。

## D. 可以直接交给 Codex 的短执行指令

> 请阅读这份任务书及 GitHub Actions `38149526876` 的真实失败日志。锁定分支当前 HEAD，先执行 PR-0 修复 `agent_events.py` Ruff I001，再执行 PR-1 对四个 React 测试文件中的 8 个失败做独立复现与最小修复。必须证明 8 个用例和整套新栈远端 CI 通过，不能弱化断言或隐藏 skip。之后依次实施 PR-2 的工具摘要安全、PR-3 的模型接入资格、PR-4 的部署控制器拆分与 Staging 演练准备、PR-5 的业务 Tool 迁移。保留 FastAPI/Pi/LiteLLM/现有 Run/Compute/Workspace/API/SSE/账本和回滚机制。所有真实生产/付费供应商/Staging 操作需先向我申请单独授权。每阶段独立提交并交付 RED/GREEN 证据、最终 Actions 链接与回滚说明。
