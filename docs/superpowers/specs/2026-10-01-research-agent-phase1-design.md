# Research Agent 第一阶段设计

## 目标与边界

在 PSKit 2.0 仓库内并行建立 `new_frontend/` 与 `new_backend/`，交付一个可独立运行的科研 Agent 工作台演示。现有 `frontend/`、`backend/` 和科学 Worker 继续作为旧系统存在。新前端是 React + Vite + TypeScript SPA；新后端是 Python FastAPI。两者只通过版本化 HTTP 和 SSE 契约通信。第一阶段的 Agent、MCP 工具、AlphaFold3 和科研产物均由可预测的 mock 驱动，不启动真实模型或 GPU 任务。

产品入口以对话为中心：用户选择项目和会话，添加 Skill、资源和文件上下文，看到 Agent 的工具活动、长任务进度、产物与剩余额度。视觉使用浅色、薄荷绿、克制的边框和阴影；桌面包含导航栏、项目侧栏、对话区和可折叠活动面板，窄屏按任务优先级收纳。

## 目录与职责

```text
new_frontend/
  src/app/             路由、Providers、布局
  src/api/             类型化 Client、HTTP 实现、浏览器 Mock 实现
  src/features/auth/   登录与身份展示
  src/features/chat/   消息、事件流、Composer、工具卡片
  src/features/usage/  Token/GPU 额度视图
  src/features/workspace/ 项目、会话、文件、Skill、资源、产物
  src/components/ui/  通用 UI primitives
  src/styles/          设计变量和响应式样式
new_backend/
  app/api/             FastAPI 路由与 SSE
  app/contracts/       Pydantic 请求、响应和事件模型
  app/domain/          会话、Run、配额与任务规则
  app/ports/           身份、模型用量、MCP、AF3 抽象
  app/adapters/mock/   可预测演示实现
  app/adapters/live/   按环境启用的 Supabase/New API 适配器
  tests/               通过 HTTP 公共接口验证行为
contracts/             固定的 OpenAPI 快照与接口说明
```

模块之间不得导入旧 `backend/app`。第一阶段允许使用内存状态，但 mock 数据必须按用户隔离，并明确重启后重置。前端不会存放 New API 管理密钥或 GPU 调度凭据。

## 身份与计费

Supabase Auth 是生产身份提供方：邮箱密码和 Google OAuth 由浏览器发起，前端向 Python API 发送访问令牌；后端验证令牌并以 Supabase `sub` 作为用户 ID。未配置 Supabase 时启用显式标识的演示登录。演示登录不冒充真实 Google OAuth，不授予外部资源访问权。

New API 负责模型请求的用量与价格配额。其“配额点数”与原始 Token 数分开记录；前端从 PSKit API 读取归一化的用户用量，不直接持有 New API 用户令牌。`UsageSnapshot` 包含：

- `tokens`: `limit`, `used`, `reserved`, `remaining`, `unit="tokens"`, `period`, `resets_at`；按用户限制原始模型 Token 数。
- `model_credits`: `granted`, `used`, `remaining`, `unit="new_api_quota"`；用于展示 New API 计费额度，不与 Token 混算。
- `gpu`: `limit`, `used`, `reserved`, `remaining`, `unit="gpu_minutes"`, `period="day"`, `resets_at`；按用户每日限制 GPU 分钟。

额度检查在后端发生。AF3 mock 提交时先预留预计 GPU 分钟；完成后以 mock 实际值结算，失败或取消释放未使用的预留。余额不足返回稳定错误码 `TOKEN_QUOTA_EXCEEDED` 或 `GPU_DAILY_QUOTA_EXCEEDED`，前端显示剩余额度和下次重置时间。每日边界由后端返回的 `resets_at` 表示，客户端不自行推断时区。

## HTTP 与事件契约

所有业务端点位于 `/api/v1`。第一阶段至少覆盖：

| 方法和路径 | 作用 |
| --- | --- |
| `POST /auth/demo`、`GET /me` | 演示登录、获取当前用户 |
| `GET /usage` | 当前用户 Token、New API 配额点和 GPU 用量 |
| `GET /projects`、`GET /projects/{id}/sessions` | 工作区导航 |
| `GET /sessions/{id}/messages`、`POST /sessions/{id}/messages` | 消息快照与创建 Run |
| `GET /runs/{id}/events?after={event_id}` | 带事件 ID 的 SSE 增量流和断线续读 |
| `GET /skills`、`GET /resources`、`GET /artifacts` | Composer 选择与结果展示 |
| `GET /mcp/tools`、`POST /mcp/tools/{name}/invoke` | mock MCP 工具目录及快速调用 |
| `POST /af3/jobs`、`GET /af3/jobs/{id}` | mock AF3 排队、运行、完成或失败 |

消息采用 `parts` 判别联合，而非单个 `content` 字符串。第一阶段事件采用 `type` 判别联合，覆盖 `message.delta`、`task.updated`、`artifact.created`、`usage.updated` 与 `run.completed`；后续 Agent runtime 再增加 `tool.*`、`message.completed` 和 `error`。每条事件包含可续读的 `id`、`run_id` 和时间。MCP mock 可独立调用；AF3 mock 返回任务 ID，在客户端查询任务时推进排队、运行与完成状态，再生成后续事件和最终回答。刷新页面后，前端读取消息与产物快照。

前端的 `Api` 接口有 `mock` 与 `http` 两种实现；UI 组件只依赖该接口和 TanStack Query，不直接 `fetch`。Mock 实现可在没有 Python 服务时运行；HTTP 实现可连接新 FastAPI。SSE 读取、重连与事件归并集中在 chat feature。Zustand 只保存 Composer 草稿、面板状态等本地 UI 状态。`assistant-ui` 限于聊天 primitives；Skill、文件、配额和活动面板保留产品自己的类型及组件。

## 页面和交互

路由包括 `/`、`/projects`、`/projects/:projectId/sessions/:sessionId`、`/skills`、`/resources`、`/settings`。工作台提供：项目与会话切换、分段消息、MCP 工具卡、AF3 后台进度、产物清单、Token 与每日 GPU 额度、Composer 的 `+` 菜单、`/` Skill 选择、`@` 资源选择、文件 chip，以及左右面板的收起与调整。文件上传第一阶段使用 mock 上传接口并返回 `file_id`，消息只携带引用。

演示数据和动作必须标记为“演示模式”。前端 mock 与后端 mock 使用相同请求、响应形状，覆盖正常流程、额度不足和事件游标续读。Google 按钮只在配置 Supabase 模式时出现，并触发真实 OAuth；演示模式使用邮箱免密码登录。

## TDD 与验收

已确认的测试边界是用户可操作页面、公开 HTTP API 和事件流消费接口。每个行为按红、绿循环做一个纵向切片；只 mock 外部 Supabase、New API、MCP、AF3 边界，不 mock 自有领域规则。后端用 FastAPI TestClient 验证按用户隔离、额度预留/结算、SSE 续读与错误码；前端用 Testing Library/Vitest 验证登录、Composer 上下文、事件显示、额度不足及响应式主流程。必要时用 Playwright 做一条浏览器演示流程。

完成标准：`new_frontend/` 和 `new_backend/` 各自有单独启动命令；无外部凭据可跑完整 mock 演示；前端可切换 mock 与 HTTP 适配器；类型检查、lint、单测、构建通过；接口快照和 README 说明模式切换、额度单位与后续接入点。

## 后续接入边界

真实 Pi Agent、GPU Worker、MCP 服务、Supabase 项目凭据与 New API 用户令牌同步属于后续阶段。适配器接口和环境变量在第一阶段定义，生产模式若缺配置应明确失败，不能静默回退到演示身份或免费额度。New API 令牌到 Supabase 用户的映射只能留在服务端。
