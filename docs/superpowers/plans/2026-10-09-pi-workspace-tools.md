# Pi 受控工作区工具与产物闭环实施计划（阶段 B）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Pi 在保持 `--no-builtin-tools` 的情况下，通过受控内部 API 使用当前 Session 的文件、搜索、命令和 Python 能力，并把生成文件回收成现有 Rich UI 产物。

**Architecture:** Pi extension 注册与常见内置工具接近的语义接口，但所有操作都使用 Run 范围 token 调用 Python。Python 重新验证用户、Session、Run、attempt、配额和 provider capability，再调用阶段 A 的 `WorkspaceSandboxProvider`。物理路径是 `/workspace/<session_id>/`；命令视角仅暴露该目录为 `/workspace`。

**Tech Stack:** Pi RPC/JavaScript extension、FastAPI、Pydantic v2、PostgreSQL、OpenSandbox provider、现有 SSE 事件和 artifact/file API。

**Spec:** `docs/superpowers/specs/2026-10-09-opensandbox-gvisor-pi-tools-design.md`

**Depends on:** 阶段 A 的 provider 端口、持久 store、真实 capability report 和失败关闭 readiness。

## Global Constraints

- Pi 继续以 `--no-builtin-tools` 启动；不得启用 Pi 原始本机 `read/write/bash`。
- 工具 token 必须绑定 `run_id + user_id + session_id + attempt_id`，浏览器不能直接调用内部 workspace API。
- 所有逻辑路径相对于当前 Session；拒绝绝对路径、`..`、NUL、反斜杠逃逸、设备文件、symlink 和跨 Session 路径。
- 上传路径固定 `/workspace/<session_id>/files/<file_id>/<safe_name>`；用户可写目录只有 `work/`、`attempts/<attempt_id>/` 和 `artifacts/<attempt_id>/`。
- `bash/python` 只有当阶段 A capability 中 `actual_runtime=runsc`、`session_mount_namespace=true`、`command_execution=true` 全部成立时才注册。
- 无 provider、provider 故障、配额不足或能力不满足时返回结构化不可用错误，禁止后端 subprocess fallback。
- command stdout/stderr 必须来自 execd 的真实增量事件；完成前不显示复制按钮或伪造终态。
- 按当前会话要求，本计划不新增或运行自动化测试；验证命令和 Staging 端到端验收在用户明确要求验证后执行。

## Review Focus

1. **内部授权：** 窃取一个 Run token 不能访问其他 Session、attempt 或用户工作区。
2. **路径竞态：** 校验后替换成 symlink 或并发 edit 不能越界或覆盖较新版本。
3. **命令可见范围：** 同一用户另一个 `/workspace/<other_session>` 必须对当前命令不可见。
4. **真实流式与取消：** SSE 事件来自 provider；取消只有 provider 确认退出后才标记 cancelled。
5. **产物闭环：** 只回收当前 attempt 的正式产物，幂等注册且继续受存储额度限制。

---

### Task B1: Session 路径策略和 attempt 持久记录

**Files:**
- Create: `new_backend/app/domain/workspace_paths.py`
- Create: `new_backend/app/db/postgres_migrations/012_workspace_attempts.sql`
- Modify: `new_backend/app/db/postgres_migrations/__init__.py`
- Create: `new_backend/app/domain/workspace_attempts.py`
- Create: `new_backend/app/contracts/workspace_tools.py`

**Interfaces:**
- `WorkspacePathPolicy` 只产生 `/<session_id>/files|work|attempts|artifacts` 下的 provider 路径。
- `workspace_attempts` 保存 attempt、run、session、user、状态、fencing token、started/finished、exit code、usage 和 provider error code。
- Pydantic contracts 覆盖 read/write/edit/list/find/grep/command/python/cancel 及增量事件。

- [ ] 把 Session ID、file ID、attempt ID 和相对路径校验集中到一个无 I/O policy；所有工具共用，不重复拼路径。
- [ ] `files/<file_id>/<safe_name>` 的 safe name 只用于展示；真实 file ID 和 digest 决定唯一目录，禁止覆盖不同内容。
- [ ] attempt 状态使用 queued/running/cancelling/completed/failed/cancelled/unknown；timeout 或断联不能宣称已停止。
- [ ] 记录 wall/cpu/memory、exit code 和截断标记；数据库不保存 stdout/stderr 正文。
- [ ] Commit: `feat(workspace): define session paths and attempts`。

### Task B2: Provider-neutral 文件传输和原子文件操作

**Files:**
- Modify: `new_backend/app/services/workspace_transfer.py`
- Create: `new_backend/app/services/workspace_files.py`
- Modify: `new_backend/app/api/sandbox_files.py`
- Modify: `new_backend/app/domain/sandboxes.py`

**Interfaces:**
- `WorkspaceTransfer` 依赖 `WorkspaceSandboxProvider`，不依赖 `SandboxPiRunner.workspace_request`。
- `WorkspaceFiles.read/write/edit/list/find/grep` 接收已解析 user/session/attempt 上下文和逻辑路径。
- `edit` 使用 expected digest/revision；`write` 采用临时对象和原子 publish。

- [ ] 上传前从 catalog 重新检查用户所有权、Session 归属、大小和 SHA256，再写入 `files/<file_id>/<safe_name>`；上传目录对模型只读。
- [ ] read 支持 offset/limit 和文本编码声明，单次返回有字节上限；二进制返回 metadata，不把任意大 base64 放入 context。
- [ ] write/edit 只允许三个可写区域；冲突返回明确 revision mismatch，不静默覆盖。
- [ ] list/find/grep 限制深度、匹配数、总字节和执行时间，结果路径统一为 Session 相对路径。
- [ ] 保留现有 `LocalWorkspace` 供旧 manager 回退，但 OpenSandbox 环境不实例化或调用它。
- [ ] Commit: `feat(workspace): proxy bounded file operations`。

### Task B3: Run 绑定的内部 workspace API

**Files:**
- Create: `new_backend/app/api/internal_workspace.py`
- Modify: `new_backend/app/api/internal.py`
- Modify: `new_backend/app/domain/internal_auth.py`
- Modify: `new_backend/app/main.py`

**Interfaces:**
- `/internal/workspace/files/read|write|edit|list|find|grep`
- `/internal/workspace/commands/start|cancel|status`
- `/internal/workspace/python/start`
- 仅接受后端签发的 agent tool token；不接收 sandbox ID、volume、UID/GID、镜像或 mount。

- [ ] 扩展 token claim/查找逻辑，把请求绑定到当前 Run 保存的 user/session/attempt；请求体出现不一致 ID 时拒绝。
- [ ] 每次调用重新检查 Run 未终止、Session 归属、provider owner、工具能力、并发和额度。
- [ ] 为文件和命令使用不同 operation allowlist；provider API key 和 execd token只存在 adapter 内部。
- [ ] 统一映射 provider 错误为稳定 code，不把 SDK URL、token 或远端堆栈返回给 Pi。
- [ ] 在 app factory 注入 provider、path policy、attempt store 和 services；workspace disabled 时路由仍存在但失败关闭。
- [ ] Commit: `feat(workspace): expose run-scoped internal tools`。

### Task B4: 命令、Python、配额和真实增量事件

**Files:**
- Create: `new_backend/app/services/workspace_commands.py`
- Modify: `new_backend/app/services/agent.py`
- Modify: `new_backend/app/api/runs.py`
- Modify: `new_backend/app/domain/quota.py`
- Modify: `new_backend/app/contracts/streaming.py`

**Interfaces:**
- `WorkspaceCommands.start(argv|shell, context)`、`stream(attempt_id)`、`cancel(attempt_id)`。
- Agent 流投影新增 workspace tool delta、usage、exit 和 artifact events，复用现有 tool/progress part。

- [ ] command 启动前预占 CPU 核毫秒，固定 cwd 为当前 attempt，固定 UID/GID 和 reserved env，限制 wall time、stdout/stderr、进程数和内存。
- [ ] `python` 使用固定解释器 argv/code 接口，不使用后端 `subprocess`；`bash` 不接受登录 shell 或用户覆盖启动参数。
- [ ] provider SSE 每段到达后立即写入现有 Run event stream；不得在完成后再模拟打字。
- [ ] 取消先进入 cancelling，调用 provider kill 后等待进程终态；失联进入 unknown 并保留已用额度。
- [ ] 结算只写入现有 PSKit 配额账本；OpenSandbox metrics 是 measured source，不成为第二个额度源。
- [ ] provider capability 不满足时完全不暴露命令工具，即使文件工具可用。
- [ ] Commit: `feat(workspace): execute metered commands through opensandbox`。

### Task B5: Pi extension 注册受控工具

**Files:**
- Modify: `new_backend/pi/extension.js`
- Modify: `new_backend/app/adapters/live/pi_rpc.py`
- Modify: `new_backend/app/services/agent.py`

**Interfaces:**
- Pi 工具：`read`、`write`、`edit`、`ls`、`find`、`grep`；能力允许时再注册 `bash`、`python`。
- 环境增加 `PSKIT_SESSION_ID`、`PSKIT_ATTEMPT_ID`、`PSKIT_WORKSPACE_CAPABILITIES_JSON`，仍通过现有内部 API URL 与 Run token。

- [ ] extension 的工具描述明确逻辑根是当前 Session `/workspace`，返回路径也使用这个视角。
- [ ] 复用 `internalJson` 的认证和安全错误处理；不向模型传 OpenSandbox URL、sandbox ID 或 token。
- [ ] read/write/edit 返回紧凑文本与 revision；搜索和命令返回截断标记、exit/usage 和可重试状态。
- [ ] Pi runner 继续附加 `--no-builtin-tools`；代码审查中应看不到启用原始内置工具的分支。
- [ ] Agent system prompt 说明上传、work、attempt、artifacts 的用途，并要求正式下载文件写入当前 artifact 目录。
- [ ] Commit: `feat(agent): register controlled workspace tools`。

### Task B6: 产物收集、聊天 Rich UI 和上下文交接

**Files:**
- Modify: `new_backend/app/services/workspace_transfer.py`
- Modify: `new_backend/app/services/agent.py`
- Modify: `new_backend/app/api/catalog.py`
- Modify: `new_backend/app/api/sandbox_files.py`
- Modify: `new_backend/app/domain/sandboxes.py`
- Modify: `new_backend/COMPUTE_SERVICES.md`

**Interfaces:**
- 每轮只收集 `/workspace/<session_id>/artifacts/<attempt_id>/`。
- 注册后的 artifact 使用现有 `/api/v1/artifacts` 预览/下载 API 和现有 Rich UI part，不增加文件路径直链。

- [ ] provider 列举产物时拒绝 symlink、目录、越界、超数量、单文件超限和单轮总量超限。
- [ ] 下载字节后复核 size/SHA256，再幂等写入 artifact store；重复回收不生成重复卡片。
- [ ] 成功注册后发出结构化 `artifact.created`；保存消息和直播投影使用同一 typed part。
- [ ] “Continue with Agent”等上下文只传 artifact/file ID、名称、mime 和摘要，不把服务器路径当可访问资源。
- [ ] 记录 OpenSandbox 工作区与远程 AF3/CORAL 的职责边界，避免把 GPU Job 误放进用户沙箱。
- [ ] Commit: `feat(workspace): collect sandbox artifacts into chat`。

## Stage B Exit Gate

- [ ] 阶段 A capability gate 已通过；若 session mount namespace 未通过，则只交付文件工具，`bash/python` 保持未注册。
- [ ] 用户明确要求验证后，才执行内部授权、路径竞态、跨 Session 隔离、实时 SSE、取消终态、配额和产物下载验证。
- [ ] 完整链路目标：上传文件 → Pi read → 生成 Markdown/CSV/DOCX → artifact 注册 → 历史消息和实时消息显示同一下载卡片。
- [ ] OpenSandbox 故障时，普通无工作区聊天和 AF3/CORAL 长任务仍可工作，且没有 local execution fallback。

