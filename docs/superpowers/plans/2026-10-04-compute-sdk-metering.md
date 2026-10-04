# 通用计算与模型接入 SDK Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 同门以 Python 函数或现有 HTTP/MCP 服务接入统一 Job，返回 Completed/Pending/Failed 与明确来源的 CPU/GPU 用量、预算授权、可靠回传和 Agent 唤醒。

**Architecture:** 推广现有 `agent_jobs`、AF3 lease/fencing 与结果持久机制，版本化服务目录和用量账本。AF3 旧路径作为兼容 adapter；通用 receiver 和薄 SDK 校验和适配维护者返回协议、提供执行上下文，不新增独立 AF3/GPU 计费真相源。

**Tech Stack:** 现有 FastAPI/Pydantic/psycopg、PostgreSQL、MCP 1.x、Python 子进程、worker 本地 journal。

**Spec:** [统一对接方案](/home/jhli/pskit-2.0/docs/research/2026-10-04-sandbox-compute-integration-proposal.md)，第 4–6、8、9 节。

## Global Constraints

- 继承 [总计划](/home/jhli/pskit-2.0/docs/superpowers/plans/2026-10-04-sandbox-compute-admin.md)全部约束。
- quota day 为 UTC；新计量整数 `cpu_core_ms / gpu_device_ms`，Task TTL 不控制执行预算。
- CPU 额度用于纳入计账的计算任务；Pi 沙箱容器 CPU 总量先单独作为用户级监控，不给并发 session 重复计账。
- 旧 AF3 分钟按 60000 转换并标 `legacy_wall`；历史归提交日，新的显式用量窗口按日切片。新增 CPU 缺省必须配置，不自动无限放行。
- 中央授权与预占限制准入；deadline 不等于远程模型强制停止。服务声明 cooperative/confirmed_stop/none 与 soft/hard；硬限制只允许独立可停止 executor，不要求所有 wrapper 启动 supervisor。
- UsageReport 六个整数指标均可为 null，source 必填；严格拒绝负数、浮点、布尔、数字字符串。required_usage 校验终态的非空值；Pending 只带 job_id，不提前生成终态 usage。
- 心跳是累计值；终态带完整累计 usage。跨日按明确 usage 窗口切片，否则采用窗口起始日归账并明确口径；不能从总 wall_ms 推测 GPU 分布。service_reported 可用于经批准的内部服务额度，来源保留，不能声称外部核验。
- Job 不强制有 Session/AgentRun；工具页需要 Pi 分析时才关联 AgentRun，所有 LLM 调用经过 Token 账本。
- 同门只能提供函数/服务，不持用户数据库/管理员权限；服务身份与用户身份分别验证。

## Review Focus

- 同一幂等键不同输入：B-1 返回冲突，不复用错误任务。
- 多连接超额提交、跨日、旧 AF3 未结算：B-2 不漏算与重复计费。
- 迟到回调/丢 ACK/attempt 过期：B-3/B-5 拒绝错误覆盖、重放原 receipt。
- 异步 CUDA/共享服务不能强杀单请求：B-4 明确计量与软限制能力。
- Pending 与重复完成唤醒：B-6 只恢复正确 Pi 上下文一次，不伪造用户消息。

---

### Task 1: B-1 — 服务目录与通用 Job API

**Files:** 新建 `app/contracts/compute.py`、`app/domain/compute/catalog.py`、`app/domain/compute/jobs.py`、`app/api/compute.py`、`app/db/postgres_migrations/004_compute.sql`；修改 migrations、`app/main.py`、`app/config.py`；测试 `tests/test_compute_contract.py`、`tests/postgres/test_compute_jobs.py`（包含实际 HTTP API 验收）。

**Interfaces:** `ComputeServiceManifest / CapabilityVersion / ComputeJob / ExecutionGrant / UsageReport / UsageReceipt` 为 Pydantic 合约；`ComputeJobs.submit(user_id, request, idempotency_key) -> ComputeJob`、`get(user_id, job_id) -> ComputeJob|None`、`cancel(user_id, job_id) -> ComputeJob`。API 为 `/api/v1/compute/jobs`、`/{id}`、`/{id}/cancel`。

同文件定义 `ComputeJobRequest / ComputeBudget / Reservation / ComputeUsage / WorkerIdentity / WorkerResources / GrantUpdate / ExecutionResult / ReceiverOutcome / InternalComputeSubmit / ComputeResumeContext`，复用 `app/contracts/catalog.py` 的现有 ArtifactRef。毫秒字段为非负 int；预算含 CPU/GPU 最大值，Job 包含 owner、版本、status、accounting_status 和可选上下文关联。ExecutionReport 是 Completed/Pending/Failed 判别 union（保留 ExecutionResult 类型别名便于 adapter）；Job 状态增加 cancelling，未知计量用 accounting_status 表示，不伪装已释放。新 Job 的身份输入只在服务器方法参数，公开 request 不能传 owner。

- [x] **Red:** `test_submit_requires_published_owned_capability`：未发布 404，跨用户 job 404；`test_idempotent_compute_submission`：同输入同 key 返回同 ID，不同输入 409；`test_tool_run_does_not_require_chat_session`：无 session/run 提交成功但保真实 owner。
- [x] **Run:** `.venv/bin/python -m pytest tests/test_compute_contract.py tests/postgres/test_compute_jobs.py -q`，每个行为先确认失败。
- [x] **Green:** 扩展 `agent_jobs`（保留旧 ID、AF3 默认 capability 与字段），新增服务/不可变能力版本/幂等记录；绑定 owner、Schema、策略快照；服务 bootstrap 使用服务器受控导入，UI 发布在 C-3 实现。
- [x] **Compatibility:** AF3 查询增加 capability 约束，避免把新通用 Job 解析成 Af3Job；先验证兼容读路径，再启用新提交。准备 feature-disable/排空回退方式，不把旧 schema v3 镜像直接当回退制品。
- [x] **Verify:** tests 通过；隔离 PG 两连接提交仍一个 Job，无 Schema 中的 user_id 可以覆盖可信 owner。
- [x] **Commit:** `feat(compute): register capabilities and expose durable owned jobs`。

### Task 2: B-2 — 统一 CPU/GPU 预占、跨日与结算

**Files:** 新建 `app/domain/compute/metering.py`、`app/domain/compute/ledger.py`；修改 `app/domain/persistent_conversation/af3.py`、`usage.py`、migration 004；测试 `tests/postgres/test_compute_ledger.py`、已有 AF3 quota 测试。

**Interfaces:** `ComputeLedger.reserve(connection, user_id, job_id, budget, now)`、`accept_usage(connection, job_id, seq, usage, window=None, terminal=False)`、`release(connection, job_id)`、`usage_for(user_id) -> ComputeUsage`。`ComputeLeases.complete` 将结果/结算/UsageReceipt 在同一事务提交。`GET /api/v1/compute/usage` 返回 CPU/GPU used/reserved/remaining 和来源，旧 `/usage` 保留分钟展示兼容。预算追加接口尚未开放；现有内部 extend helper 需补策略检查与 Job 快照同步后再启用。

- [x] **Red:** `test_two_submissions_cannot_overspend_last_gpu_window`：限额 60000ms，两请求各预占 40000ms，只准入一个。`test_cumulative_usage_is_not_double_charged`：seq 1=20000ms、seq 2=30000ms、重传 seq 2，used=30000ms。`test_cross_day_keeps_unsettled_reservation`：UTC 日切后旧 hold 仍存在，计量区间按两天分摊。
- [x] **Run:** `.venv/bin/python -m pytest tests/postgres/test_compute_ledger.py -q`，真实隔离 PG；逐个确认失败。
- [x] **Green:** 与旧 AF3 共用事务准入锁，唯一报告键和 hash；整毫秒累计、预算 window、跨日 reservation、failed/cancel 已用量结算。旧 AF3 原行直接以 legacy_wall 来源参与唯一聚合，不复制为第二组收费行；保留旧 Token 账本。两条准入入口均保留所有日期未结算 hold。
- [x] **Verify:** 旧 AF3 used/reserved 数值兼容；新 GPU 120000ms 显示 2 分钟，CPU 40000ms 显示 40 核秒。减少额度不删除已有 reservation，未知用量不按零结算。
- [x] **Commit:** `feat(compute): unify resource reservations and precise usage settlement`。

### Task 3: B-3 — 通用 worker claim、grant、结果 receipt

**Files:** 新建 `app/api/internal_compute.py`、`app/domain/compute/leases.py`；修改 `app/domain/persistent_conversation/af3.py`、`app/services/agent.py`；测试 `tests/postgres/test_compute_claims.py`（包括 worker HTTP 合约）。outbox 持久表复用既有 Run scheduler 消费。

**Interfaces:** `/internal/compute/jobs/claim|{id}/heartbeat|{id}/result`；`claim(worker, resources) -> ExecutionGrant|None`、`heartbeat(grant, report) -> GrantUpdate`、`complete(grant, report) -> UsageReceipt`。grant 包含 attempt/fencing/device UUID/预算/固定 stop_at；receipt 包含 accepted_seq/hash/终态。

- [x] **Red:** `test_stale_attempt_cannot_overwrite_result`：旧 attempt 409；`test_duplicate_result_returns_original_receipt`：同 payload 同 receipt，冲突 payload 409；`test_heartbeat_does_not_extend_absolute_deadline`：renew 后 stop_at 不变。`test_one_gpu_uuid_has_one_exclusive_lease`：不同模型不能重复占同设备。
- [x] **Run:** `.venv/bin/python -m pytest tests/postgres/test_compute_claims.py -q`。
- [x] **Green:** 服务凭据认证、claim fencing、通用 GPU UUID 锁、单调用量、停止请求/确认状态；结果/账本/outbox 同事务。旧 `/internal/compute/af3` 保字段兼容、共享配额聚合；物理设备锁迁入通用协议前必须排空旧 receiver，不以兼容读路径宣称物理调度已合并。
- [x] **Verify:** 网络失联/lease 过期标待确认，不自动并发重跑；取消意图先 `cancelling`，可信退出报告才终态和释放剩余预占。
- [x] **Commit:** `feat(compute): fence workers and acknowledge durable result receipts`。

### Task 4: B-4 — 薄 Python SDK 与统一返回协议

**Files:** 新建 `new_backend/pskit_compute/{__init__,service,context}.py`；修改 `pyproject.toml` 包发现；示例 `examples/compute_service.py`；测试 `tests/test_compute_sdk.py`。

**Interfaces:** `ComputeService(service_id, model_version)`、`compute_tool(name, required_usage, ...)`、`manifest()`、`execute(grant, arguments) -> ExecutionReport`；`ExecutionContext` 仅提供进度/取消钩子。类型复用 contracts/compute，不复制 Schema。

- [x] **Red/Green:** 函数签名生成输入 Schema 且排除 ctx/身份；维护者返回 Completed 的结果和 usage 原样保留；异步函数及 Pending 支持；Failed 保留已用量；抛错返回 unknown usage，不捏造 GPU 零用量。
- [x] **Validation:** 非法单位、未知字段、缺少 required_usage、output_schema 错误拒绝；缺少必需计量的终态不能作为可发布服务验收通过。
- [x] **Implementation:** 包装器只校验与转换；不强制 cgroup、NVML、CUDA、子进程或容器。可选本地 supervisor 后续独立模块接入，不是同门接入前置条件。
- [x] **Verify:** `.venv/bin/python -m pytest tests/test_compute_sdk.py -q`；安装包含 SDK；示例展示普通函数、已有服务报告与已消耗资源的失败。
- [x] **Commit:** `feat(compute): add thin SDK for typed execution reports`。

### Task 5: B-5 — 持久 receiver 与 HTTP/MCP adapter

**Files:** 新建 `pskit_compute/receiver.py`、`journal.py`、`http_adapter.py`、`mcp_adapter.py`、`new_backend/scripts/compute_receiver.py`；测试 `tests/test_compute_receiver.py`、`tests/test_compute_service_adapters.py`。旧 af3_receiver 保留原环境。

**Interfaces:** `Receiver.run_once() -> ReceiverOutcome`、`Journal.record(grant, ComputeResultRequest)`、`acknowledge(UsageReceipt)`、`recover()` 恢复持久 grant/payload/pending/state；HTTP adapter 配置 submit/status/cancel URL；MCP adapter 使用现有客户端世代并映射 Completed/Pending/Failed。

- [x] **Red:** `test_lost_result_ack_replays_without_inference`：重启后执行次数仍 1，重传原报告；`test_journal_deletes_only_matching_committed_receipt`：错误 receipt 不清理。`test_adapter_does_not_measure_network_wait_as_gpu`：没有服务计量则标 unknown，不写真实 GPU 值。
- [x] **Run:** `.venv/bin/python -m pytest tests/test_compute_receiver.py tests/test_compute_service_adapters.py -q`。
- [x] **Green:** SQLite WAL 只作为执行主机本地 journal，与中央 PostgreSQL 主账本职责分离；progress/outcome 原子持久、ACK hash 校验、停止预算；保 AF3 环境和启动逻辑，通用模型使用新 receiver 回传。
- [x] **Verify:** 断网、重复、损坏记录、主机恢复和取消确认；同门只需配置函数/HTTP 对应关系，不要求新深度学习框架。
- [x] **Commit:** `feat(compute): persist receiver outbox and adapt model service transports`。

### Task 6: B-6 — 通用 Pending → Pi 停止 → 结果自动唤醒

**Files:** 修改 `app/services/agent.py`、`app/domain/persistent_conversation/recovery.py`、`runs.py`、`new_backend/pi/extension.js`；新建 `tests/postgres/test_compute_agent_wakeup.py`、`tests/test_compute_pi_rpc.py`。

**Interfaces:** MCP adapter 业务结果 union Completed/Pending/Failed；既有 `PersistentConversationStore.claim_wakeups`、`finish_pi_turn` 复用 Run lease 并消费 compute_outbox；Pi submit_compute 停止，终态通过自定义事件恢复。旧直接 MCP invoke 保持原 completed 合约，长任务需改走 adapter/receiver/submit_compute，避免建立第二套异步机制。

- [x] **Red:** `test_pending_generic_tool_stops_then_resumes_same_session`：提交时停止模型循环，完成后原 session 一次分析。`test_duplicate_completion_does_not_resume_twice`：两次回调一个恢复。`test_tool_page_job_without_run_does_not_create_chat`：独立 Job 仅更新状态，主动分析动作才进入 Pi。
- [x] **Run:** `.venv/bin/python -m pytest tests/postgres/test_compute_agent_wakeup.py tests/test_compute_pi_rpc.py -q`；保现有 AF3 resume 测试。
- [x] **Green:** 统一 durable wait 关系、outbox、自动结果注入；当前 MCP 保业务 Pending，不升级成未协商 Tasks；工具页 LLM 经过已有 Run model proxy 的 Token 准入。
- [x] **Verify:** 用户离线、沙箱重建、失败/取消结果和多个待完成 Job；Session、Job、用量、回复 owner 一致。
- [x] **Commit:** `feat(agent): resume sessions from generic compute completion events`。

### Task 7: B-7 — OpenAPI、模型维护者文档与隔离验收

**Files:** 修改 `scripts/export_openapi.py`、`deploy/agent/SANDBOX.md`；新建 `new_backend/COMPUTE_SERVICES.md`、`deploy/agent/scripts/compute_smoke.py`；测试沿用本阶段 HTTP/PG/SDK 公开边界。

**Interfaces:** OpenAPI 导出 Job/manifest/usage/receipt；维护者文档包含函数、HTTP、worker 三种路径，CPU/GPU 口径与硬/软限制声明。

- [x] **Red/Green:** 在 existing API 合约测试中逐个加入导出类型缺失断言，修复 Schema 导出；不另测试私有算法。
- [x] **Verify:** 本阶段测试+ruff；固定镜像隔离 PostgreSQL、实际本地 CPU/HTTP 执行、quota 拒绝、丢 ACK、取消、真实 Pi RPC/本地模型替身；AF3 兼容路径 regression。记录 PostgreSQL skip、替身和真实执行区别，不以这些结果宣称整套生产 Compose/GPU 已联调。
- [x] **Document:** 同门只需提交 manifest/API 样例和配置服务账户；实际模型权重/启动命令待各维护者提供，不以示例服务冒充全部科研模型已接入。
- [x] **Commit:** `docs(compute): publish verified model integration and metering contracts`。

## 本次执行边界与迁移决定

用户明确要求修改后直接开始，任务 1–7 在当前工作区逐项执行并分批提交。只完成 B，不以 A/C 页面或真实 GPU 部署为前置条件。实施前 PostgreSQL 为 v3；已实现下一版 v4（004_compute.sql）；旧 SQLite 只用于已有离线测试，计算模块主存储使用 PostgreSQL。新 worker service 配置由服务器受控 manifest 与独立凭据导入；没有配置时目录为空，不生成演示服务。Admin Test 在 C 扩展，本阶段提供相同 SDK 验收检查。


## 2026-10-04 实施结果

B-1 至 B-7 已实现并分批提交；最终验证记录见 [计算 SDK 交付记录](/home/jhli/pskit-2.0/docs/research/2026-10-04-compute-sdk-delivery.md)。全后端 503 项通过、无跳过，ruff 与前端 typecheck/lint/build 通过，SDK wheel 构建通过。

本阶段保留的范围：旧 AF3 使用来源明确的配额兼容聚合，物理设备调度尚未迁入通用 claim；直接 MCP invoke 不新增第二套异步机制。预算追加公开 API 尚未开放，内部 helper 需补策略检查后再使用。真实模型接口、GPU 计量/硬停止、通用产物上传、A/C 及生产发布另行交付。
