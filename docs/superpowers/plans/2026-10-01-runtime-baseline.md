# 新版运行基线与模式矩阵（2026-10-01）

本文件记录当前实现的接口行为，供后续外部联调和回归使用；它不是生产验收证明。权威接口 Schema 在 `contracts/openapi.json`，SSE 事件 Schema 在 `contracts/run-event.schema.json`。

## 运行模式

| 配置 | 身份 | Agent | MCP 默认 | AF3 默认 | 模型入口 |
| --- | --- | --- | --- | --- | --- |
| `mode=mock, agent_runtime=mock` | Python 演示身份 | 演示回复 | mock | mock | 无 |
| `mode=mock, agent_runtime=pi` | Python 演示身份 | Pi RPC，持久 SQLite | mock | mock | Pi 配置或本地替身 |
| `mode=live, agent_runtime=mock` | Supabase 邮箱/Google | 演示回复 | disabled | disabled | 无 |
| `mode=live, agent_runtime=pi` | Supabase 邮箱/Google | Pi RPC，持久 SQLite | disabled | disabled | 服务端 OpenAI 兼容入口必填 |

MCP 可显式选 `mock/remote/disabled`；AF3 可显式选 `mock/callback/disabled`。`callback` 要求 Pi 运行时及计算回调密钥。身份、MCP、AF3 分别配置；没有计算节点时，`callback` 模式只能完成接口契约验证，不能生成真实预测。

## HTTP 与事件基线

- 公共 API 前缀为 `/api/v1`。身份提供登录、注册、验证、刷新、退出及 Google OAuth；工作区提供项目、会话、消息；能力 API 提供 MCP、AF3、文件、产物、用量与管理员限额。各路由和请求模型以 OpenAPI 快照为准。
- `POST /sessions/{id}/messages` 接受幂等键，返回 `run_id`。Pi Run 在 SQLite 入队、原子领取，并受全局/用户并发上限约束。
- `GET /runs/{id}/events?after=N` 使用 SSE；事件序号按 Run 递增，断线可从游标补读。每批最多读取 128 条，发送时遵守客户端背压；`follow=false` 返回当时快照。浏览器不需要任务轮询。
- 已类型化事件包括 `message.*`、`tool.*`、`plan.created/updated`、`approval.*`、`task.updated`、`artifact.created`、`usage.updated`、`run.*`。`plan.*` 每次包含完整步骤快照；相同快照不重复写入。
- Pi 通过 `pi --mode rpc` 的 JSONL stdin/stdout 交互；当前会话文件持久化在本机路径。Pi 提交异步 AF3 后返回 pending 并停止本轮，计算完成由 Python 调度重新发送内部恢复命令。

## 配额基线

- 用户 Token 按月计，消息入场先估算预留，模型报告用量后调整；后台恢复前再次预留。当前尚未做到模型每次调用前的严格上限。
- GPU 按日计，AF3 提交时预留预计分钟，完成/失败回调按实际分钟结算。`callback` 模式的未领取任务排队超过 `RESEARCH_AGENT_AF3_QUEUE_TIMEOUT_SECONDS` 会失败并释放预留；已领取任务的真实耗时对账尚未定义。
- `/usage` 和 `/usage/entries` 返回 Python 记录的额度与流水，不以模型网关私有点数作为前端单位。

## 可重复验证

- 后端：`cd new_backend && python -m pytest -q`；其中本地 Pi 网关契约测试需要允许 loopback 套接字。
- 前端：`cd new_frontend && npm run typecheck && npm run lint && npm run build && npm run test -- --run`。
- Pi 扩展：`cd new_backend && node --test pi/extension.test.js`。
- 契约：`cd new_backend && PYTHONPATH=. python scripts/export_openapi.py`，随后运行 `tests/test_exported_event_contract.py`。

真实 Supabase 邮件/Google 回调、模型网关、MCP 服务和 GPU AF3 计算节点均未提供，因此这些集成仍需另行联调。
