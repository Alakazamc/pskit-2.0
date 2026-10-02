# Pi Agent 与后台任务唤醒设计

## 目标

在 `new_backend/` 中增加可选的 Pi RPC 运行模式。用户提交消息后，Python 创建 Run；Pi 负责推理与工具选择。AF3 暂用后台 mock：工具提交任务后返回 `pending` 并结束当前 Pi 推理；任务完成后，Python 自动向同一 Pi 会话注入内部完成事件，继续生成结果。网页关闭时任务仍继续。`new_frontend/` 沿用现有消息、事件与配额接口。

旧 `backend/` 和 `frontend/` 不参与。默认 `mock` 模式及第一阶段演示保持可用。Pi 模式必须显式启用；Pi 不存在或配置缺失时明确失败，不退回静态回复。

## 已确认的边界

- Python FastAPI 是 HTTP、身份、Run、任务、配额和事件的唯一入口。
- Pi 通过 JSONL RPC 子进程工作。每个会话有单独的 Pi session 文件；Python 持久保存文件映射。Pi 进程不持有运行中的 GPU 作业。
- Pi 自带文件和 shell 工具在该模式下禁用；项目扩展只注册显式允许的 mock 能力。后续 Skill 与 MCP 能力从同一扩展边界接入。
- SQLite 持久保存消息、Run、事件游标、任务状态和用户额度。单进程部署是本阶段边界；多实例调度留到外部队列阶段。
- New API 真实模型调用配置、真实 AF3、Supabase 凭据和多机 Worker 不在本阶段。Pi 模型配置由运行环境提供，不在前端保存密钥。

## 最小运行链

1. `POST /sessions/{id}/messages` 检查用户与额度、写入用户消息和 Run，返回 `run_id`。
2. 后台 Run 执行器启动 `pi --mode rpc`，发送 `prompt`，持续读取事件直到 `agent_settled`。`message_update` 转成已有 `message.delta`；最终回答写入消息快照。
3. Pi 扩展的 `submit_af3` 工具通过仅限内部调用的 HTTP 入口提交 mock 任务，返回 `{status:"pending", task_id}` 与 `terminate:true`。Python 保存任务与关联的 Run，Run 进入 `waiting`。
4. Python 定时推进 mock 任务，不依赖浏览器轮询。完成时结算每日 GPU 分钟、记录产物、发出 `task.updated` 和 `artifact.created`，并将 Run 标记为待唤醒。
5. 唤醒执行器从持久状态领取一次性唤醒工作，在原 Pi session 上执行扩展命令。扩展写入 `customType="pskit.task_completed"` 的内部消息并触发新一轮推理。它不是用户消息。最终回答完成后写入消息与 `run.completed`。
6. 重启后扫描待执行 Run 和待唤醒任务。领取采用状态比较更新，重复完成通知不会产生重复唤醒或重复最终回答。运行中 Pi 进程崩溃时记录可见错误，允许安全重试；具体模型请求不保证恰好执行一次。

## 数据与接口

Run 状态：`queued | running | waiting | resume_queued | completed | failed`。任务状态：`queued | running | completed | failed`。每个任务关联 `user_id`、`session_id`、`run_id` 和 Pi `tool_call_id`；任务 ID 是幂等键。用户仅能读取自己的消息、事件和任务。内部提交入口使用进程内签发的临时令牌，绑定当前 Run；浏览器令牌不能调用。

已有外部 HTTP 路径及 `RunEvent` 形状保持兼容。`GET /runs/{id}` 提供持久状态；新增 `run.failed` 事件用于明确显示错误，任务进度沿用 `task.updated`；前端继续按 `after` 游标补读。配额仍分别表示原始 Token、New API 配额点和每日 GPU 分钟。Pi 模式本阶段仅按输入长度预扣 Token，真实模型用量对账是后续 New API 接入任务，界面必须说明该估算。

## 错误与约束

Pi 启动失败、RPC 响应失败或子进程意外退出使 Run 进入 `failed`，写入错误事件；不能生成成功回答。GPU 额度不足时工具返回明确失败，不创建任务。唤醒失败保留完成任务和待唤醒状态，按有限次数重试。未知任务、跨用户任务、过期内部令牌返回 404 或 401。SQLite 写入与任务领取使用事务。

## 验收边界

沿用用户已确认的公开 HTTP、事件流和前端消费接口测试边界。用假 Pi RPC 子进程验证 JSONL 协议、完整回答、工具挂起、自动唤醒、重启后恢复、重复完成幂等、跨用户隔离与配额结算。对 Pi 扩展的工具结果与内部唤醒命令作独立 Node 测试。每个行为按红、绿循环实现。没有 Pi 可执行文件或模型凭据的环境仍能验证完整 mock 流程，但真实 Pi 端到端运行需在配置齐全的环境单独验收。

## Pi API 依据

- [Pi RPC](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/rpc.md)：JSONL、命令响应与 `agent_settled`。
- [Pi 扩展](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/extensions.md)：自定义工具、`terminate`、内部消息。
- [Pi durable](https://github.com/earendil-works/pi/blob/main/packages/durable/README.md)：当前标注为实验性；本阶段不依赖其不稳定接口。
