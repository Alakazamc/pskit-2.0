# 用户沙箱 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让每用户 CPU 沙箱可安全复用多个会话，活动时不被回收，文件、产物与 transcript 能持久恢复。

**Architecture:** 扩展已有 manager/bridge 和 SandboxPiRunner；Docker 仍是唯一 provider。生命周期状态持久化，bridge 持有可观察的活动执行，Python 保持用户/会话所有权与文件授权。

**Tech Stack:** 现有 Python/FastAPI/httpx、Docker、Pi 0.87.1、PostgreSQL。

**Spec:** [统一对接方案](/home/jhli/pskit-2.0/docs/research/2026-10-04-sandbox-compute-integration-proposal.md)，第 2、3、8 节。

## Global Constraints

- 继承 [总计划](/home/jhli/pskit-2.0/docs/superpowers/plans/2026-10-04-sandbox-compute-admin.md)全部约束。
- 继续默认 1 CPU、1 GiB RAM、256 PIDs 和 1800 秒空闲窗口；资源档位读取服务器配置，不接受浏览器 Docker 参数。
- 用户 volume 与容器生命周期独立；停止或升级不删卷；owner 不匹配不能采用已有容器或卷。
- 仅后端可调用 manager。会话串行，不同会话可并发，保留现有用户/全局并发额度。
- 首版保留 Pi 已注册工具边界；任意 shell/Python 开放需额外隔离验收，不用 env 清理宣称实现完整沙箱安全。

## Review Focus

- 活跃 Run 超过空闲窗口：A-1 验证不停止。
- manager 重启、bridge 失联：A-2 验证未知活动保守处理。
- 同 session 双请求、取消与重连：A-2 验证串行和可靠终态。
- 路径穿越、symlink、超限文件：A-3 验证拒绝且不覆盖授权文件。
- 镜像更新与 transcript 回退：A-4/A-5 验证 drain 后保卷恢复。

---

### A-1: 持久活动 lease 与空闲回收

**Files:** 新建 `new_backend/app/contracts/sandbox.py`、`new_backend/app/domain/sandboxes.py`、`new_backend/app/db/postgres_migrations/004_sandboxes.sql`；修改 `app/db/postgres_migrations/__init__.py`、`app/sandbox_manager.py`、`app/adapters/live/sandbox_pi.py`、`app/config.py`；测试 `tests/test_sandbox_lifecycle.py`、`tests/postgres/test_sandboxes.py`。

**Interfaces:** `SandboxActivityStore.acquire(owner_id, session_id, run_id, lease_seconds) -> SandboxLease`、`renew(lease_id, fencing_token) -> SandboxLease`、`release(lease_id, fencing_token) -> None`、`activity(owner_id) -> SandboxActivity`。manager 增加 `/v1/sandboxes/activity/acquire|renew|release`，租约超时只标未知，不据此证明 Pi 退出。

`contracts/sandbox.py` 定义 `SandboxLease / SandboxActivity / SandboxPromptRequest / SandboxAttemptStatus / SandboxSummary / SandboxOperation / SandboxMetrics / WorkspaceFileRef`；所有身份参数为 str，时长参数为正 int 秒，revision 为非负 int。manager 的数据库 DSN 仅来自私有服务器配置；内存替身只用于测试，不作为线上活动事实来源。

- [ ] **Red:** `test_active_turn_survives_idle_sweep`：时钟推进 1801 秒，活动 lease 存在时 sweep `stopped=0`；结束并再空闲 1801 秒才 `stopped=1`。`test_same_user_sessions_reuse_volume`：两个会话同 owner 一个容器，另一用户不同容器。
- [ ] **Run:** `cd new_backend && .venv/bin/python -m pytest tests/test_sandbox_lifecycle.py -q`，确认因缺活动契约失败。
- [ ] **Green:** 实现上述接口，migration 保存 owner/instance/活动 lease/最后完成时间；runner 开始前 acquire，执行中 renew，确定退出后 release。Docker 边界仅伪造网络响应。
- [ ] **Verify:** 同命令通过；设置隔离 `TEST_POSTGRES_DSN` 后运行 `tests/postgres/test_sandboxes.py` 验证两连接并发准入与重启恢复，不接受 skip。
- [ ] **Commit:** `feat(sandbox): keep active session leases during idle reclamation`。

### A-2: bridge 的执行、取消和恢复状态

**Files:** 新建 `app/services/sandbox_sessions.py`；修改 `app/sandbox_bridge.py`、`app/adapters/live/sandbox_pi.py`、`app/adapters/live/pi_rpc.py`；测试 `tests/test_sandbox_bridge.py`、`tests/test_sandbox_pi_runner.py`、`tests/test_sandbox_recovery.py`。

**Interfaces:** bridge 增加 `GET /v1/pi/activity`、`POST /v1/pi/attempts/{id}/cancel`；prompt 包含后端绑定的 `attempt_id`。`SandboxSessionCoordinator.prompt(request: SandboxPromptRequest, on_event)`、`cancel(attempt_id: str) -> SandboxAttemptStatus`，活动列表不返回 token 或输入正文。

- [ ] **Red:** `test_concurrent_same_session_is_serialized`：同 session 两请求不能同时进入 Pi，同用户不同 session 可并行。`test_manager_restart_checks_bridge_before_reaping`：bridge 回报活动则不停止，失联标 `unknown`。`test_cancel_waits_for_pi_exit`：请求取消后状态为 `cancelling`，确认进程退出才 `cancelled`。
- [ ] **Run:** `.venv/bin/python -m pytest tests/test_sandbox_recovery.py tests/test_sandbox_bridge.py -q`，逐个新增行为确认失败。
- [ ] **Green:** coordinator 管单会话锁和 attempt；bridge 断连时完成取消并记录退出结果；manager 回收前检查可信活动，不以最近 ensure 或心跳过期代替退出证明。
- [ ] **Verify:** 上述测试、现有 runner 测试通过；不同会话返回各自 session_file，不交叉 transcript。
- [ ] **Commit:** `feat(sandbox): coordinate session attempts and confirmed cancellation`。

### A-3: 上传文件进入 workspace，产物回到 Artifact

**Files:** 新建 `app/services/workspace_transfer.py`、`app/api/sandbox_files.py`；修改 `app/sandbox_bridge.py`、`app/services/agent.py`、`app/adapters/live/sandbox_pi.py`；测试 `tests/test_workspace_transfer.py`、`tests/test_sandbox_files.py`。

**Interfaces:** `WorkspaceTransfer.prepare(user_id, session_id, file_ids) -> list[WorkspaceFileRef]`、`collect(user_id, session_id, attempt_id) -> list[ArtifactRef]`；bridge `/v1/workspace/files` 接收固定相对名/sha256/content，产物导出在同 session/attempt 下。公开接口只使用 file/artifact ID。

- [ ] **Red:** `test_owned_upload_is_readable_in_session_workspace`：同 SHA 的文件进入正确目录，另一用户 file ID 返回 404。`test_workspace_transfer_rejects_escape`：`../`、绝对路径、symlink 与超限拒绝。`test_output_roundtrip_preserves_artifact_owner`：下载字节一致，重传同 digest 不重复注册。
- [ ] **Run:** `.venv/bin/python -m pytest tests/test_workspace_transfer.py tests/test_sandbox_files.py -q`，每个 tracer 首先失败。
- [ ] **Green:** 后端绑定所有权、目录清单和大小；文件临时写入后原子 rename，产物经授权导出入现有 Artifact 存储；损坏/超限不修改已存在文件。
- [ ] **Verify:** tests 通过；检查并发会话只同步各自授权文件，不扫描整个用户卷。
- [ ] **Commit:** `feat(sandbox): transfer owned workspace files and artifacts`。

### A-4: 沙箱列表、资源观测与排空换镜像

**Files:** 修改 `app/contracts/sandbox.py`、`app/domain/sandboxes.py`、`app/sandbox_manager.py`；新建 `app/services/sandbox_operations.py`；测试 `tests/test_sandbox_operations.py`。

**Interfaces:** `SandboxOperations.list() -> list[SandboxSummary]`、`drain(owner_id, expected_revision) -> SandboxOperation`、`replace_keep_volume(owner_id, image_digest) -> SandboxOperation`、`read_usage(owner_id) -> SandboxMetrics`。metrics 的容器 CPU 总量明确是用户级监控，不重复按 session 计费。

- [ ] **Red:** `test_image_replace_waits_for_active_attempts`：活动时 `draining`，新请求 409；结束后新容器挂原 volume。`test_owner_mismatch_never_reuses_volume`：标签不匹配 409。`test_sandbox_summary_has_no_credentials`：无 token、环境或敏感输入。
- [ ] **Run:** `.venv/bin/python -m pytest tests/test_sandbox_operations.py -q`，确认失败。
- [ ] **Green:** 使用固定镜像与 owner 标签；operation/revision 持久化；停止后更换容器保卷，任何失败保留可恢复状态，禁止删卷来解冲突。
- [ ] **Verify:** 上述测试通过，旧的 ensure/reuse 合约仍通过。
- [ ] **Commit:** `feat(sandbox): drain runtimes and preserve user volumes on upgrade`。

### A-5: 私有网络与隔离 Compose 验收

**Files:** 修改 `deploy/agent/compose.sandbox.yaml`、`deploy/agent/SANDBOX.md`；新建 `deploy/agent/sandbox-gateway.conf`、`deploy/agent/scripts/sandbox_smoke.py`；测试 `tests/test_sandbox_compose_contract.py`、`tests/test_sandbox_environment.py`。

**Interfaces:** sandbox 仅连接独立 internal 网络和受控 gateway，网关只代理已授权 internal model/tool 路径；Pi 环境明确 allowlist，排除 manager/bridge/global provider secret。smoke 接收独立 compose project、测试 user/session，拒绝生产项目。

- [ ] **Red:** `test_sandbox_cannot_join_database_network`、`test_pi_environment_contains_only_scoped_tokens`；Compose/环境契约失败后逐项实现，不以环境清理代替不同 UID/内核隔离。
- [ ] **Run:** `.venv/bin/python -m pytest tests/test_sandbox_compose_contract.py tests/test_sandbox_environment.py -q`。
- [ ] **Green:** 配置独立网络、最小代理路径、镜像 digest、持久卷与转移 adapter；更新启动和本地回退说明，保留当前受限工具模式。
- [ ] **Verify:** 相关测试和 ruff；在隔离 Compose 实际执行两用户三会话、文件往返、停止/恢复、排空保卷、网络拒绝；记录真实 Docker 证据，与替身测试分开。
- [ ] **Commit:** `feat(sandbox): isolate execution networking and document verified lifecycle`。
