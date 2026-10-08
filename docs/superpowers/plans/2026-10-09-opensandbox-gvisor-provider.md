# OpenSandbox 与 gVisor Provider 实施计划（阶段 A）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变生产 Pi 工具集合的前提下，为新版 PSKit 增加固定版本、失败关闭、可持久化的 OpenSandbox/gVisor 工作区 provider。

**Architecture:** Pi RPC 继续运行在受信任的 Python 控制面。新增独立的 `WorkspaceSandboxProvider` 端口和 OpenSandbox 实现，按用户维护一个 gVisor 沙箱及持久卷；`pi_execution` 不再决定工作区 provider。旧自研 Docker manager 保留用于回退和历史数据导出，但不能与 OpenSandbox 同时为同一环境提供执行能力。

**Tech Stack:** Python 3.12、FastAPI、PostgreSQL、OpenSandbox Python SDK 1.1.0、OpenSandbox Server 1.1.0、Docker、gVisor `runsc`、Docker Compose。

**Spec:** `docs/superpowers/specs/2026-10-09-opensandbox-gvisor-pi-tools-design.md`

## Global Constraints

- 固定 `opensandbox==1.1.0`、`opensandbox-server==1.1.0`；server、execd、egress 和 sandbox 镜像发布时解析并记录完整 digest，禁止 `latest`。
- OpenSandbox Server 必须启用 API key、私网监听和 `secure_runtime.type=gvisor`；运行时检查不到 `runsc` 时启动失败。
- 用户数据物理布局固定为 `/workspace/<session_id>/`，不创建 `session/` 或 `sessions/` 中间目录。
- 每用户一个外层沙箱和持久卷；不同 Session 共享外层用户边界，但命令执行必须隐藏其他 Session。
- 模型驱动进程固定为 UID/GID `10001:10001`，不能覆盖 reserved env、镜像、挂载、runtime、网络或资源参数。
- provider 不得把 API key、execd token、模型 key、JWT、文件正文写入日志、数据库展示字段或工具结果。
- `disabled` 与 `opensandbox` 是本阶段唯一工作区模式。配置错误、运行时不符或 provider 不健康时失败关闭，禁止 fallback 到 local subprocess、旧 Docker manager 或 `runc`。
- 本阶段只建立 provider、持久状态和能力探测，不注册 Pi 文件或命令工具。
- 按当前会话要求，本计划不新增或运行自动化测试；验证命令和 Staging 冒烟在用户明确要求验证后执行。
- 只暂存本计划列出的文件；保留当前 `docs/DEV_CLOUD_CONTEXT.md` 与 `docs/performance/` 的既有未提交改动。

## Review Focus

1. **配置耦合：** `RESEARCH_AGENT_PI_EXECUTION=local` 时仍能选择 OpenSandbox 工作区；选择 OpenSandbox 不会把 Pi 搬进用户容器。
2. **运行时降级：** server 声称 gVisor 但 Docker 实际使用 `runc` 时 readiness 必须失败，不能只检查配置文本。
3. **并发创建：** 同一用户的两个首次请求只能形成一个有效映射；孤儿实例由明确的 reconciliation 处理。
4. **实例与数据身份：** sandbox ID 可替换，用户卷 ID 持久；stop/replace 不能删除卷。
5. **Alpha API 漂移：** 所有 OpenSandbox SDK 调用集中在一个适配器，业务服务不依赖 SDK 类型或异常。

---

### Task A1: 固定依赖并建立 provider 端口

**Files:**
- Modify: `new_backend/pyproject.toml`
- Create: `new_backend/app/ports/workspace_sandbox.py`
- Create: `new_backend/app/adapters/disabled/workspace_sandbox.py`
- Modify: `new_backend/app/config.py`

**Interfaces:**
- `WorkspaceSandboxProvider.ensure_user(user_id) -> WorkspaceSandbox`
- `WorkspaceSandboxProvider.capabilities() -> WorkspaceCapabilities`
- `WorkspaceSandboxProvider.readiness() -> WorkspaceReadiness`
- `WorkspaceSandboxProvider.stop_user(user_id)`、`replace_user(user_id, expected_revision)`
- `WorkspaceSandboxProvider.files`、`commands` 分别承载文件和执行能力；上层不导入 OpenSandbox SDK 类型。

- [ ] 把 OpenSandbox SDK 固定到 1.1.0，并在部署制品中记录解析后的依赖版本。
- [ ] 定义 provider-neutral DTO、错误分类和异步协议：unavailable、unsafe runtime、conflict、timeout、capacity、invalid path。
- [ ] 新增独立配置 `RESEARCH_AGENT_WORKSPACE_PROVIDER=disabled|opensandbox`，以及私有 server URL、API key、固定镜像 digest、namespace、超时、CPU/RAM/PID/磁盘/inode 上限。
- [ ] 保留 `RESEARCH_AGENT_PI_EXECUTION` 只描述 Pi 进程位置；在配置校验中禁止 OpenSandbox 缺 key、非私网 URL、可变镜像 tag、非正资源限制或未知 provider。
- [ ] 提供 disabled 实现，使普通聊天保持可用，同时任何 workspace 调用明确返回 `WORKSPACE_UNAVAILABLE`。
- [ ] Commit: `feat(sandbox): define opensandbox provider boundary`。

### Task A2: 建立独立持久状态与并发收敛

**Files:**
- Create: `new_backend/app/db/postgres_migrations/011_workspace_sandboxes.sql`
- Modify: `new_backend/app/db/postgres_migrations/__init__.py`
- Create: `new_backend/app/domain/workspace_sandboxes.py`
- Modify: `new_backend/scripts/agent_data_migrate.py`

**Interfaces:**
- `workspace_sandboxes` 保存 `user_id/provider/sandbox_id/volume_id/image_digest/provider_revision/lifecycle_state/runtime_state/last_confirmed_at`。
- `workspace_sandbox_leases` 保存 `attempt_id/session_id/run_id/fencing_token/state/expires_at`。
- `WorkspaceSandboxStore.claim_creation`、`confirm_instance`、`acquire_lease`、`renew_lease`、`release_after_exit`、`begin_replace`、`confirm_replace`。

- [ ] 新建表而不复用 `sandbox_owners` 的自研 manager 语义；旧表和旧数据保持可读。
- [ ] 使用 PostgreSQL 行锁和 revision/fencing token 收敛同一用户并发 ensure，明确记录 creating、ready、draining、replacing、error。
- [ ] 把 lease expiry 定义为 unknown 而非退出证明；只有 provider 确认进程终态后才能 release。
- [ ] sandbox ID 与 volume ID 分离；replace 只替换实例并复用卷，任何正常路径都不删除卷。
- [ ] 把新表加入受控数据导出/导入清单，但不迁移旧测试 sandbox 数据。
- [ ] Commit: `feat(sandbox): persist opensandbox ownership and leases`。

### Task A3: 实现 OpenSandbox SDK 适配器和能力探测

**Files:**
- Create: `new_backend/app/adapters/live/opensandbox_workspace.py`
- Create: `new_backend/app/adapters/live/opensandbox_compat.py`
- Create: `new_backend/app/services/workspace_sandbox_lifecycle.py`

**Interfaces:**
- `OpenSandboxWorkspaceProvider` 实现 Task A1 端口。
- `OpenSandboxCompat` 是 SDK 1.1.0 唯一入口，封装 create/connect/files/commands/metrics/kill/stop/delete-instance。
- `WorkspaceSandboxLifecycle` 负责 store fencing、孤儿协调、空闲 stop 和 drain/replace。

- [ ] 对 SDK 1.1.0 做一次只读 API 能力映射，把上游请求/响应、异常和 SSE 事件转为项目 DTO；业务层不得调用未封装 SDK 方法。
- [ ] ensure 时使用服务端固定镜像、volume、资源、network、UID/GID 和 labels；拒绝浏览器或模型覆盖这些字段。
- [ ] 创建后读取实际 runtime/诊断信息并确认 `runsc`；无法得到可信证明时标记 unsafe，停止实例并拒绝返回 ready。
- [ ] 探测文件 API、命令事件、取消确认、metrics、持久卷重连、用户 UID、`/proc/1/environ`、ptrace、提权和 reserved env 覆盖能力。
- [ ] 将命令可见 Session 根目录能力单独标记为 `session_mount_namespace`；不满足时 `command_execution=false`，不能用路径字符串检查替代。
- [ ] 所有日志只记录 request ID、用户哈希、provider 状态和错误 code，不记录 token、命令正文或文件正文。
- [ ] Commit: `feat(sandbox): add fail-closed opensandbox adapter`。

### Task A4: 接入应用生命周期和 readiness

**Files:**
- Modify: `new_backend/app/main.py`
- Modify: `new_backend/app/api/health.py`
- Modify: `new_backend/app/services/sandbox_operations.py`
- Modify: `new_backend/app/api/admin_operations.py`

**Interfaces:**
- `app.state.workspace_sandbox_provider`
- `/health/ready` 增加 `workspace_provider` 状态；provider 设为 opensandbox 时 unsafe/unreachable 返回 503。
- 管理操作通过 provider-neutral lifecycle 查询、stop、drain 和 replace。

- [ ] 在 app factory 中根据 `workspace_provider` 构造 disabled 或 OpenSandbox 实现，不再用 `pi_execution` 决定工作区是否存在。
- [ ] 启动只做轻量 capability/readiness 检查，不因查看页面为用户创建沙箱。
- [ ] readiness 明确区分 disabled、ready、degraded、unsafe；OpenSandbox 模式中 degraded/unsafe 使需要工作区的部署不可 ready。
- [ ] 把现有 `SandboxOperations` 改成 provider-neutral facade；旧 manager adapter 仅保留在旧 overlay，不参与 OpenSandbox 环境。
- [ ] 关闭应用时停止后台 lease reconciler，不删除实例或卷。
- [ ] Commit: `feat(sandbox): wire opensandbox readiness and lifecycle`。

### Task A5: 增加固定基础设施制品和失败关闭预检

**Files:**
- Create: `deploy/agent/compose.opensandbox.yaml`
- Create: `deploy/agent/opensandbox.toml.example`
- Create: `deploy/agent/sandbox/Dockerfile`
- Create: `deploy/agent/sandbox/entrypoint.sh`
- Create: `deploy/agent/scripts/opensandbox_preflight.py`
- Modify: `deploy/agent/cloud.env.example`
- Create: `deploy/agent/OPENSANDBOX.md`
- Modify: `deploy/agent/DEPLOYMENT.md`

**Interfaces:**
- Compose 只在私有网络启动 OpenSandbox Server；无宿主公网 ports。
- `opensandbox_preflight.py` 输出不含秘密的 capability report，并以非零退出拒绝缺少 `runsc`、错误 runtime、可变 tag、公开监听或失败的隔离探测。

- [ ] Compose 固定 server/execd/egress/sandbox 镜像 digest，占位符必须由发布 manifest 提供；不得隐式拉取 latest。
- [ ] 配置 OpenSandbox API key、gVisor secure runtime、私有网络、持久卷、资源上限和日志脱敏。
- [ ] sandbox image 只含 execd、shell、Python 和批准的只读运行时；不复制仓库源码、`.env`、SSH、Git、云凭据或数据库工具。
- [ ] preflight 同时检查 Docker 已注册 `runsc`、OpenSandbox 实际创建的探针沙箱使用 `runsc`、用户为 10001、无 Docker socket/后端挂载/数据库网络，并检查 stop 后卷仍可重连。
- [ ] 手册记录版本矩阵、digest 解析、私有配置位置、root 前置条件、失败关闭含义和不删除卷的回退步骤。
- [ ] Commit: `feat(deploy): add pinned opensandbox gvisor stack`。

## Stage A Exit Gate

- [ ] 源码实现与文档完成；生产 Pi 仍用 `--no-builtin-tools`，没有新增 workspace 工具。
- [ ] 用户明确要求验证后，才执行 dependency/build、数据库迁移演练、Compose render、真实 `runsc` capability probe 和隔离冒烟。
- [ ] capability report 必须证明实际 runtime、UID、网络、挂载、`/proc`、取消终态和持久卷行为；任一命令隔离项失败时 Stage B 只能继续文件工具部分。
- [ ] 提交范围不包含当前已有的 `docs/DEV_CLOUD_CONTEXT.md` 与 `docs/performance/` 改动。

