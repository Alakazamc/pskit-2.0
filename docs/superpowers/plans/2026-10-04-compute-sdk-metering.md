# 通用计算与模型接入 SDK Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 同门以 Python 函数或现有 HTTP/MCP 服务接入统一 Job，提供 CPU/GPU 用量、预算执行、可靠回传和 Agent 唤醒。

**Architecture:** 推广现有 `agent_jobs`、AF3 lease/fencing 与结果持久机制，版本化服务目录和用量账本。AF3 旧路径作为兼容 adapter；通用 receiver 和薄 SDK 提供执行上下文，不新增独立 AF3/GPU 计费真相源。

**Tech Stack:** 现有 FastAPI/Pydantic/psycopg、PostgreSQL、MCP 1.x、Python 子进程、worker 本地 journal。

**Spec:** [统一对接方案](/home/jhli/pskit-2.0/docs/research/2026-10-04-sandbox-compute-integration-proposal.md)，第 4–6、8、9 节。

## Global Constraints

- 继承 [总计划](/home/jhli/pskit-2.0/docs/superpowers/plans/2026-10-04-sandbox-compute-admin.md)全部约束。
- quota day 为 UTC；新计量整数 `cpu_core_ms / gpu_device_ms`，Task TTL 不控制执行预算。
- CPU 额度用于纳入计账的计算任务；Pi 沙箱容器 CPU 总量先单独作为用户级监控，不给并发 session 重复计账。
- 旧 AF3 分钟按 60000 转换并标 `legacy_wall`；历史归提交日，新的可信执行区间按日切片。新增 CPU 缺省必须配置，不自动无限放行。
- 中央授权、固定 deadline、停止余量和可信 supervisor 共同限制预算；共享且不能独立停止的服务只能声明软限制。
- Job 不强制有 Session/AgentRun；工具页需要 Pi 分析时才关联 AgentRun，所有 LLM 调用经过 Token 账本。
- 同门只能提供函数/服务，不持用户数据库/管理员权限；服务身份与用户身份分别验证。

## Review Focus

- 同一幂等键不同输入：B-1 返回冲突，不复用错误任务。
- 多连接超额提交、跨日、旧 AF3 未结算：B-2 不漏算与重复计费。
- 迟到回调/丢 ACK/attempt 过期：B-3/B-5 拒绝错误覆盖、重放原 receipt。
- 异步 CUDA/共享服务不能强杀单请求：B-4 明确计量与软限制能力。
- Pending 与重复完成唤醒：B-6 只恢复正确 Pi 上下文一次，不伪造用户消息。

---

### B-1: 服务目录与通用 Job API

**Files:** 新建 `app/contracts/compute.py`、`app/domain/compute/catalog.py`、`app/domain/compute/jobs.py`、`app/api/compute.py`、`app/db/postgres_migrations/005_compute.sql`；修改 migrations、`app/main.py`、`app/config.py`；测试 `tests/test_compute_jobs_api.py`、`tests/postgres/test_compute_jobs.py`。

**Interfaces:** `ComputeServiceManifest / CapabilityVersion / ComputeJob / ExecutionGrant / UsageReport / UsageReceipt` 为 Pydantic 合约；`ComputeJobs.submit(user_id, request, idempotency_key) -> ComputeJob`、`get(user_id, job_id) -> ComputeJob|None`、`cancel(user_id, job_id) -> ComputeJob`。API 为 `/api/v1/compute/jobs`、`/{id}`、`/{id}/cancel`。

同文件定义 `ComputeJobRequest / ComputeBudget / Reservation / ComputeUsage / WorkerIdentity / WorkerResources / GrantUpdate / ResumeTrigger / ExecutionResult / ExecutionOutcome / ReceiverOutcome / PendingReport`，复用 `app/contracts/catalog.py` 的现有 ArtifactRef。毫秒字段为非负 int；预算含 CPU/GPU 最大值，Job 包含 owner、版本、status、accounting_status 和可选上下文关联。ExecutionResult 是 Completed/Pending/Failed 判别 union；Job 状态增加 cancelling，未知计量用 accounting_status 表示，不伪装已释放。新 Job 的身份输入只在服务器方法参数，公开 request 不能传 owner。

- [ ] **Red:** `test_submit_requires_published_owned_capability`：未发布 404，跨用户 job 404；`test_idempotent_compute_submission`：同输入同 key 返回同 ID，不同输入 409；`test_tool_run_does_not_require_chat_session`：无 session/run 提交成功但保真实 owner。
- [ ] **Run:** `.venv/bin/python -m pytest tests/test_compute_jobs_api.py -q`，每个行为先确认失败。
- [ ] **Green:** 扩展 `agent_jobs`（保留旧 ID、AF3 默认 capability 与字段），新增服务/不可变能力版本/幂等记录；绑定 owner、Schema、策略快照；服务 bootstrap 使用服务器受控导入，UI 发布在 C-3 实现。
- [ ] **Compatibility:** AF3 查询增加 capability 约束，避免把新通用 Job 解析成 Af3Job；先验证兼容读路径，再启用新提交。准备 feature-disable/排空回退方式，不把旧 schema v3 镜像直接当回退制品。
- [ ] **Verify:** tests 通过；隔离 PG 两连接提交仍一个 Job，无 Schema 中的 user_id 可以覆盖可信 owner。
- [ ] **Commit:** `feat(compute): register capabilities and expose durable owned jobs`。

### B-2: 统一 CPU/GPU 预占、跨日与结算

**Files:** 新建 `app/domain/compute/metering.py`、`app/domain/compute/ledger.py`；修改 `app/domain/persistent_conversation/af3.py`、`usage.py`、`app/contracts/models.py`、migration 005；测试 `tests/test_compute_usage_api.py`、`tests/postgres/test_compute_ledger.py`、已有 AF3 quota 测试。

**Interfaces:** `ComputeLedger.reserve(user_id, job_id, budget, period) -> Reservation`、`extend(reservation_id, budget) -> Reservation`、`accept_usage(report, grant) -> UsageReceipt`、`settle(job_id, report) -> UsageReceipt`、`usage_for(user_id) -> ComputeUsage`。`GET /api/v1/compute/usage` 返回 CPU/GPU used/reserved/remaining 和来源，旧 `/usage` 保留分钟展示兼容。

- [ ] **Red:** `test_two_submissions_cannot_overspend_last_gpu_window`：限额 60000ms，两请求各预占 40000ms，只准入一个。`test_cumulative_usage_is_not_double_charged`：seq 1=20000ms、seq 2=30000ms、重传 seq 2，used=30000ms。`test_cross_day_keeps_unsettled_reservation`：UTC 日切后旧 hold 仍存在，计量区间按两天分摊。
- [ ] **Run:** `.venv/bin/python -m pytest tests/postgres/test_compute_ledger.py -q`，真实隔离 PG；逐个确认失败。
- [ ] **Green:** 用户/资源/日事务锁，唯一报告键和 hash；整毫秒累计、预算 window、跨日 reservation、failed/cancel 已用量结算。旧 AF3 行写入唯一 legacy 来源并在同事务切换聚合，防止 AF3 与新 ledger 双计；保留旧 Token 账本。
- [ ] **Verify:** 旧 AF3 used/reserved 数值兼容；新 GPU 120000ms 显示 2 分钟，CPU 40000ms 显示 40 核秒。减少额度不删除已有 reservation，未知用量不按零结算。
- [ ] **Commit:** `feat(compute): unify resource reservations and precise usage settlement`。

### B-3: 通用 worker claim、grant、结果 receipt

**Files:** 新建 `app/api/internal_compute.py`、`app/domain/compute/leases.py`、`app/domain/compute/outbox.py`；修改 `app/domain/persistent_conversation/af3.py`、`app/api/internal.py`；测试 `tests/test_compute_worker_protocol.py`、`tests/postgres/test_compute_claims.py`。

**Interfaces:** `/internal/compute/jobs/claim|{id}/heartbeat|{id}/result`；`claim(worker, resources) -> ExecutionGrant|None`、`heartbeat(grant, report) -> GrantUpdate`、`complete(grant, report) -> UsageReceipt`。grant 包含 attempt/fencing/device UUID/预算/固定 stop_at；receipt 包含 accepted_seq/hash/终态。

- [ ] **Red:** `test_stale_attempt_cannot_overwrite_result`：旧 attempt 409；`test_duplicate_result_returns_original_receipt`：同 payload 同 receipt，冲突 payload 409；`test_heartbeat_does_not_extend_absolute_deadline`：renew 后 stop_at 不变。`test_one_gpu_uuid_has_one_exclusive_lease`：不同模型不能重复占同设备。
- [ ] **Run:** `.venv/bin/python -m pytest tests/test_compute_worker_protocol.py tests/postgres/test_compute_claims.py -q`。
- [ ] **Green:** 服务凭据认证、claim fencing、资源锁、单调用量、停止请求/确认状态；结果/账本/outbox 同事务。旧 `/internal/compute/af3` 保字段兼容并适配同 Job/ledger。
- [ ] **Verify:** 网络失联/lease 过期标待确认，不自动并发重跑；取消意图先 `cancelling`，可信退出报告才终态和释放剩余预占。
- [ ] **Commit:** `feat(compute): fence workers and acknowledge durable result receipts`。

### B-4: 薄 Python SDK、计量与可信 supervisor

**Files:** 新建 `new_backend/pskit_compute/__init__.py`、`service.py`、`context.py`、`supervisor.py`、`metering.py`；修改 `new_backend/pyproject.toml` 的包发现；新建 `new_backend/examples/compute_service.py`；测试 `tests/test_compute_sdk.py`、`tests/test_compute_supervisor.py`。

**Interfaces:** `ComputeService(service_id, model_version)`、`compute_tool(name, policy_ref)`、`manifest() -> ComputeServiceManifest`、`execute(grant, arguments) -> ExecutionResult`；`ExecutionContext.report_progress/check_cancelled/save_artifact`。`ProcessSupervisor.execute(command, grant, on_usage) -> ExecutionOutcome`，command 仅来自维护者服务器配置。

- [ ] **Red:** `test_decorator_preserves_signature_but_excludes_context`：工具输入只有 sequence，不包含 ctx/identity。`test_failed_function_still_reports_usage`：抛错也有终态 usage。`test_supervisor_stops_before_grant_expires`：有停止余量，退出确认后才停止 GPU 分配计时。`test_shared_executor_cannot_claim_hard_cancel`：共享不可隔离配置拒绝硬限制声明。
- [ ] **Run:** `.venv/bin/python -m pytest tests/test_compute_sdk.py tests/test_compute_supervisor.py -q`，外部 clock/process/计量源是明确替身边界。
- [ ] **Green:** 函数 Schema 派生、可信 ctx 注入；CPU cgroup 计数、GPU slot 占用区间、单调钟。原模型保自己的环境；监管 executor 是独立可信进程。cgroup 或停止能力不可用时显式 unavailable/soft，不伪装精确硬限制。
- [ ] **Verify:** 真实隔离 CPU 子进程停止验收另记录；GPU 只验证协议替身，不声明真实 CUDA 释放已验收。实例示例无 API key、假生产数据或隐式推理执行。
- [ ] **Commit:** `feat(compute): add typed model wrapper and supervised execution contract`。

### B-5: 持久 receiver 与 HTTP/MCP adapter

**Files:** 新建 `pskit_compute/receiver.py`、`journal.py`、`http_adapter.py`、`mcp_adapter.py`、`new_backend/scripts/compute_receiver.py`；修改 `scripts/af3_receiver.py`；测试 `tests/test_compute_receiver.py`、`tests/test_compute_service_adapters.py`。

**Interfaces:** `Receiver.run_once() -> ReceiverOutcome`、`Journal.record(grant, report)`、`acknowledge(receipt)`、`recover() -> list[PendingReport]`；HTTP adapter 配置 submit/status/cancel/result；MCP adapter 使用现有客户端世代并映射 Completed/Pending/Failed。

- [ ] **Red:** `test_lost_result_ack_replays_without_inference`：重启后执行次数仍 1，重传原报告；`test_journal_deletes_only_matching_committed_receipt`：错误 receipt 不清理。`test_adapter_does_not_measure_network_wait_as_gpu`：没有服务计量则标 unknown，不写真实 GPU 值。
- [ ] **Run:** `.venv/bin/python -m pytest tests/test_compute_receiver.py tests/test_compute_service_adapters.py -q`。
- [ ] **Green:** SQLite WAL 只作为执行主机本地 journal，与中央 PostgreSQL 主账本职责分离；progress/outcome 原子持久、ACK hash 校验、停止预算；保 AF3 环境和启动逻辑，适配通用 receiver 回传。
- [ ] **Verify:** 断网、重复、损坏记录、主机恢复和取消确认；同门只需配置函数/HTTP 对应关系，不要求新深度学习框架。
- [ ] **Commit:** `feat(compute): persist receiver outbox and adapt model service transports`。

### B-6: 通用 Pending → Pi 停止 → 结果自动唤醒

**Files:** 修改 `app/contracts/capabilities.py`、`app/api/capabilities.py`、`app/api/internal.py`、`app/services/agent.py`、`app/domain/persistent_conversation/recovery.py`、`new_backend/pi/extension.js`；新建 `tests/test_compute_agent_wakeup.py`。

**Interfaces:** MCP 业务结果 union Completed/Pending/Failed；`ComputeOutbox.claim_wakeups(worker_id) -> list[ResumeTrigger]`、`ack_wakeup(trigger_id, run_id)`；Pi 扩展统一后台结果事件，不冒充原 toolResult 未返回或用户消息。

- [ ] **Red:** `test_pending_generic_tool_stops_then_resumes_same_session`：提交时停止模型循环，完成后原 session 一次分析。`test_duplicate_completion_does_not_resume_twice`：两次回调一个恢复。`test_tool_page_job_without_run_does_not_create_chat`：独立 Job 仅更新状态，主动分析动作才进入 Pi。
- [ ] **Run:** `.venv/bin/python -m pytest tests/test_compute_agent_wakeup.py -q`；保现有 AF3 resume 测试。
- [ ] **Green:** 统一 durable wait 关系、outbox、自动结果注入；当前 MCP 保业务 Pending，不升级成未协商 Tasks；工具页 LLM 经过已有 Run model proxy 的 Token 准入。
- [ ] **Verify:** 用户离线、沙箱重建、失败/取消结果和多个待完成 Job；Session、Job、用量、回复 owner 一致。
- [ ] **Commit:** `feat(agent): resume sessions from generic compute completion events`。

### B-7: OpenAPI、模型维护者文档与隔离验收

**Files:** 修改 `scripts/export_openapi.py`、`deploy/agent/SANDBOX.md`；新建 `new_backend/COMPUTE_SERVICES.md`、`deploy/agent/scripts/compute_smoke.py`；测试沿用本阶段 HTTP/PG/SDK 公开边界。

**Interfaces:** OpenAPI 导出 Job/manifest/usage/receipt；维护者文档包含函数、HTTP、worker 三种路径，CPU/GPU 口径与硬/软限制声明。

- [ ] **Red/Green:** 在 existing API 合约测试中逐个加入导出类型缺失断言，修复 Schema 导出；不另测试私有算法。
- [ ] **Verify:** 本阶段测试+ruff；隔离 Compose 跑实际 CPU 任务、quota 拒绝、丢 ACK、取消、Pi 唤醒；AF3 兼容路径 regression。记录所有 PostgreSQL skip、替身和真实执行区别。
- [ ] **Document:** 同门只需提交 manifest/API 样例和配置服务账户；实际模型权重/启动命令待各维护者提供，不以示例服务冒充全部科研模型已接入。
- [ ] **Commit:** `docs(compute): publish verified model integration and metering contracts`。
