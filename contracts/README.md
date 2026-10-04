# Research Agent API 契约

`openapi.json` 和 `run-event.schema.json` 由 `new_backend/scripts/export_openapi.py` 从 FastAPI 与 Pydantic 导出。公开路由使用 `/api/v1`；`/internal` 供 Pi 工具和计算 Worker 使用，不包含在公开 OpenAPI 中。前端统一通过 `new_frontend/src/api/` 访问 Python API，开发环境由 Vite 代理。

更新契约时，先在 `new_backend/` 运行 `PYTHONPATH=. python scripts/export_openapi.py`，再在 `new_frontend/` 运行 `npm run generate:api`。前者从同一 Pydantic `RunEvent` 联合类型导出 `run-event.schema.json` 和 `src/api/generated-events.ts`；前端 `RunEvent`、`PlanStep` 直接引用生成类型。后者从 OpenAPI 生成 `src/api/generated/`，使用固定版本的 `@hey-api/openapi-ts`。前端身份、项目、会话、用量、文件和产物等 DTO 已引用生成类型；`contract-typecheck.ts` 还检查部分 `ResearchApi` 返回值与生成的路由响应兼容。后端测试会校验事件 TS 文件与当前 Python 模型完全一致。

Python 提供演示和真实身份模式。演示模式可用 `/auth/demo`；真实模式的邮箱、Google OAuth、刷新与登出接口由 Python 调用 Supabase Auth。Refresh Token 保存在 HttpOnly Cookie，浏览器不会直接调用 Supabase。真实 Supabase 邮件、OAuth 回调仍需部署后联调。
匿名入口为 `POST /api/v1/auth/anonymous`，可携带 Turnstile `captcha_token`；游客绑定新邮箱和 Google 身份仍由 Python 调用 Supabase。游客配额、上传和 GPU 限制由服务端执行。长期不活跃游客由独立运维命令清理，不提供浏览器删除接口；清理期间的请求返回 `GUEST_ACCOUNT_DELETING`（410）。

Token 额度按月、GPU 分钟额度按日管理。Pi 模式在首次请求和任务完成后的恢复前原子预留 Token，再按 Pi 报告的用量调整；GPU 任务在提交时预留，完成、失败或取消时结算或释放。两种额度是不同单位，不等于模型网关的私有计费点数。单次模型调用前的硬上限和真实网关对账仍待实现。
默认 mock 模式也通过 Python `/usage` 与 `/usage/entries` 返回实际内存账本变化，包含 Token 扣用/调整和 AF3 GPU 任务状态；不由浏览器生成样例流水。mock 账本不跨进程重启持久化，Pi 模式使用 SQLite 流水。
直接提交 AF3 任务时可用 `Idempotency-Key`；同一用户、键和请求参数重试返回原任务状态，参数冲突返回 409，不会重复预留 GPU 分钟。前端 API 客户端要求调用方提供稳定请求键。Pi 内部工具调用仍使用 `run_id` 与 `tool_call_id` 防重。

SSE 使用 `id`、`event`、`data` 行；`data` 是带 `id`、`run_id`、`created_at`、`type`、`data` 的 JSON。`after` 游标用于补读；消息、计划、工具、任务、审批、用量、产物和 Run 状态均有类型化事件。`GET /api/v1/runs/{id}` 返回当前 Run 状态，其他用户的资源不可读取。

审批接口的响应总包含 `job_id`：批准后是任务 ID，拒绝时为 `null`。前端响应类型直接引用生成的 `ApprovalDecisionResponse`，更新 Python 合同时需重新导出 OpenAPI 并生成 TypeScript 类型。

MCP 可使用 mock 或一个/多个 Streamable HTTP 服务，按允许列表和 JSON Schema 校验工具调用。多服务工具名使用服务 ID 前缀。远端调用具有超时、结果大小限制和本进程并发上限。AF3 可使用 mock 或持久计算任务回调契约，包含领取、租约、心跳、产物上传和结果提交；真实 GPU 服务尚未部署。可配置能力缺失时返回稳定错误码，不能以演示结果冒充真实执行。

文件接口支持流式上传、受控大小与格式、按用户下载和删除。PDF 文字提取在受资源与时间限制的独立子进程完成。已完成计算任务的文本产物可以按用户归属预览，最多 256 KiB；原件下载仍是独立接口。持久解析队列与真实对象存储仍待实现。Pi 模式将会话、消息、事件、任务、额度与文件保存在 SQLite；备份时需停写并同时保存 Pi 会话目录。

每轮消息的 `attachments` 最多 10 个，文本、PDF 和图片共用此上限；图片仍要求所选模型支持多模态。超过上限返回 422 和 `TOO_MANY_ATTACHMENTS`，不创建消息或扣用 Token。上限按轮计算，下一轮可继续上传；单文件上传接口及用户存储配额仍分别校验大小和格式。
