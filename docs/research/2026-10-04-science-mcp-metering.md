# 科研模型接入 MCP、计算计量与配额：对接调研

日期：2026-10-04。范围：核对官方协议、阅读现有源码、提出对接契约。本文中的接口、装饰器和账本方案是**设计建议**；本次没有实现、测试、部署或修改模型服务。

## 1. 结论

建议提供一个薄的 **PSKit 科研模型 SDK + 中央计算入口**。同门保留自己的模型、Python 环境和常驻服务，只提供带类型的函数或已有 HTTP 接口。SDK 负责统一输入输出、身份上下文、任务进度和用量上报；中央 Python 后端负责准入、配额预占、任务关联、结算与 Agent 唤醒。

**MCP 负责让 Agent 发现和调用能力；GPU/CPU 配额是平台自定的执行契约。** 标准 Tools 定义函数、输入输出 Schema、结果及错误；Progress 定义进度通知；Tasks 定义异步句柄与状态。它们没有定义每日 GPU 额度、CPU core-seconds 账本或 GPU 限制执行器。把 `gpu_seconds` 放进自定义结果，不会自动获得可信计量与硬限制。这是对下述官方规范职责的核对结论，而不是 MCP 产品能力承诺。[Tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)、[Progress](https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/progress)、[Tasks 扩展](https://modelcontextprotocol.github.io/ext-tasks/specification/2026-07-28/tasks.html)

用户的 Pi 沙箱负责调用中央入口；GPU 模型留在可信计算服务中。用户容器不能持有配额管理员凭据、GPU 调度凭据或全局模型密钥，也不能通过绕过中央入口直接提交 GPU 任务。

## 2. MCP 最新规范与当前项目不是同一个协议世代

本次访问官方 `specification/latest`，它指向 **2026-07-28**。不能继续把 2025-11-25 称为最新版本。[官方规范](https://modelcontextprotocol.io/specification/latest)

| 能力 | 2025-11-25 | 2026-07-28 |
| --- | --- | --- |
| 协议上下文 | `initialize` 握手；可有 HTTP session | 每次请求携带版本与 capability；协议核心无 session |
| Streamable HTTP | POST，可用 GET SSE/session/恢复机制 | POST；JSON 或本次请求的 SSE；另有订阅请求；不支持 `Last-Event-ID` 恢复 |
| 普通请求取消 | `notifications/cancelled` | HTTP 关闭本次请求 SSE 即取消；stdio 仍用取消通知 |
| 长任务 | experimental core Tasks，旧方法集合含 `tasks/result`、`tasks/list` | 可选 `io.modelcontextprotocol/tasks` 扩展；`tasks/get`、`tasks/update`、`tasks/cancel`，`resultType: "task"` |

来源：[旧版 experimental Tasks](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks)、[新版发布说明](https://blog.modelcontextprotocol.io/posts/2026-07-28/)、[新版 Streamable HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)、[新版 Tasks](https://modelcontextprotocol.github.io/ext-tasks/specification/2026-07-28/tasks.html)。长任务取消与普通 HTTP 请求断开要分开处理：提交请求结束以后，应按任务契约取消任务，不能把关闭浏览器当成删除后台任务。

**Task TTL 与执行预算分开。** 新版 `ttlMs`（旧版 `ttl`）描述任务句柄、状态和结果的可用生命周期，服务可在过期后终止其协议状态或删除记录；它不是 GPU 时间额度，也不证明 CUDA 计算已停止。平台另存 `grant_expires_at`（执行授权截止）、`stop_at`（supervisor 强制执行截止）、`reservation_id`（配额预占）与结果保留期。任务句柄过期不能自动释放仍在运行的 GPU 预占，长结果保留期也不能延长执行授权。[Tasks 生命周期](https://modelcontextprotocol.github.io/ext-tasks/specification/2026-07-28/tasks.html)

其他与对接直接有关的标准事实：

- **Progress 是可选通知**。请求 `_meta.progressToken` 与 `notifications/progress` 关联；不能假定服务器一定发进度，也不能把进度百分比当计算用量。[Progress 规范](https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/progress)
- `_meta` 支持厂商字段，应使用自己的反向域名前缀，例如 `net.bioailab.pskit/execution`。`clientInfo` 是自报信息，不能作用户身份或计费依据。[Metadata 规范](https://modelcontextprotocol.io/specification/2026-07-28/basic/index)
- 远程授权规范要求校验 access token 的目标 audience；不能把任意 Supabase JWT 或 LiteLLM key 直接转发给同门服务，并称其为合规 MCP OAuth。[Authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)
- 浏览器与 PSKit 之间仍采用本项目的 REST/SSE；后端与模型之间可以采用 MCP。二者的 event cursor、重连与持久化规则分别定义，不能混用。

### Python SDK / FastMCP 选型

- **官方 Python SDK** 当前文档声明 v2 为 stable line，示例 API 是 `MCPServer` 和高层 `Client`。项目目前 `mcp>=1.26,<2`，不能直接套最新示例。官方迁移指南明确：手动 `ClientSession.initialize()` 是旧世代入口；新版有 discover/高层 Client 的兼容流程。[官方 SDK](https://py.sdk.modelcontextprotocol.io/)、[迁移指南](https://py.sdk.modelcontextprotocol.io/migration/)
- **独立 FastMCP** 适合写同门接入的薄服务、函数工具、代理和 middleware。最新版后台任务需要可选 `fastmcp-tasks`，使用 Docket；默认 `memory://` 重启丢失任务，持久分布式部署需要 Redis/Valkey 后端。一个 `@tool(task=True)` 不能替代现有可靠账本与本地执行记录。[FastMCP Tools](https://gofastmcp.com/servers/tools)、[后台任务及后端](https://gofastmcp.com/servers/tasks)
- 第一版对接建议复用 PSKit 的 PostgreSQL 任务表和 worker 协议，SDK 不强制增加 Redis。支持新版 Tasks 的 client/server 可以映射到同一套内部 Job；旧客户端返回自定义 `Pending(job_id)`，由平台轮询和唤醒。协议升级需要单独兼容评估，本次不升级依赖。

## 3. 同门如何接入：三个入口，共用一个执行上下文

| 同门现状 | 对接形式 | 执行与计量位置 |
| --- | --- | --- |
| 有普通 Python 函数 | SDK 装饰器生成工具 Schema；按服务启动一次 | 模型所在主机的受信任 receiver/SDK |
| 已有 FastAPI/HTTP 模型接口 | 填写 submit/status/cancel/result 的 adapter 配置 | HTTP 服务端上报真实执行用量；网关只能测网络往返时间 |
| 常驻 GPU 模型服务 | 模型进程常驻；SDK 挂在请求调度与执行边界 | 服务内独占/串行请求可计 slot 时间；共享并发要明确分摊策略 |

同门作为 **模型服务提供者**，不是向中央 MCP 发送一条请求就变成提供者：他们可以暴露 MCP server，让 PSKit client 调用；也可以注册普通 HTTP 服务，由 PSKit adapter 对 Agent 暴露为工具。已有 A6000 的主动领任务模式属于平台 worker 对接，不要求每个模型服务自行实现 MCP JSON-RPC。

建议调用链：

```text
Pi / 工具集页面
  → Python 中央入口：身份、权限、Schema、幂等、配额预占
  → MCP adapter 或受信任 receiver
  → 同门的常驻模型 / 独立执行进程
  → 可信用量记录 + artifact 引用
  → 中央结算、持久事件、Agent 唤醒
```

模型页发起的 LLM 调用仍走中央 Python → LiteLLM 的 Token 路径；计算工具调用走计算配额路径。两条路径都关联真实用户与可审计执行记录，不能因为入口是“工具集页面”而避开 Token 预占。普通模型工具执行保留 ToolRun/Job 即可；实际调用 Pi 做分析时才创建或关联 AgentRun。工具请求不要让模型自己填写可信 `user_id` 或上报“我花了多少 GPU 时间”。

## 4. “耗时”要先统一定义

| 字段/单位建议 | 含义 | 是否适合每日额度 |
| --- | --- | --- |
| `wall_ms` | 整个任务经过时间；包括 I/O 等待 | 展示任务时长；不能直接称真实 GPU 计算时间 |
| `gpu_device_ms` | 各 GPU 被该 Job **分配/占用**区间的毫秒数之和 | 推荐首版 GPU 配额：一张卡 60 秒 = 60 GPU device-seconds；两张卡同时 60 秒 = 120 |
| `cuda_elapsed_ms` | 指定 stream/event 覆盖的 CUDA 执行区间 | 性能指标；并发 kernel、stream 与多 GPU 的口径需另定 |
| `gpu_utilization_pct` | 采样区间 GPU 活跃统计 | 监控；不是可直接结算的每用户时间 |
| `gpu_memory_peak_bytes` | 峰值显存量 | 准入与监控；不是计算耗时 |
| `cpu_core_ms` | Job cgroup `cpu.stat.usage_usec` 增量除以 1000 | 可累计 CPU core-seconds；四核各忙 10 秒 = 40 core-seconds |

依据：[Linux cgroup v2 `cpu.stat`/`cpu.max`](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html)、[NVML utilization 定义](https://docs.nvidia.com/deploy/archive/R525/nvml-api/structnvmlUtilization__t.html)、[NVIDIA DCGM 指标/Job stats](https://docs.nvidia.com/datacenter/dcgm/latest/user-guide/feature-overview.html)。表中的账单字段名与分配区间公式是 PSKit 建议，不是 NVIDIA 或 MCP 的统一计费标准。

### 推荐首版计费口径

1. 排队不扣 GPU 时间；CPU 数据预处理只记 CPU，尽量到需要 GPU 时才获取 GPU slot。
2. 已取得 GPU slot 后的请求加载、推理、仍占有设备的清理时间，计入 device-seconds；实际释放以后停止计时。
3. 常驻服务的启动加载、空闲占卡是服务成本，默认不全额摊给偶然进入的单个请求；如需冷启动计费，manifest 明确列出计费阶段。
4. 用整数毫秒累计；界面换算成分钟。每个 heartbeat 向上取整一分钟会严重多收；不从百分比推算用量。
5. 子进程 CPU 用量需要放入该 Job 的 cgroup 一起统计；同一常驻进程并发处理多个请求时，进程级 CPU 计数不能自动归属于某个请求。

每用户容器复用多个 session 时，容器 `cpu.stat` 的增量只适合用户总账；多个会话并发期间，不能将同一个差值分别归给每条 Run。需要按 attempt 建独立进程树/cgroup，或者明确仅做用户级汇总，不提供伪精确的逐请求账单。

CUDA 调用通常异步返回。只在 Python 函数前后读时钟会漏掉尚未完成的 GPU 操作；受控单任务执行可在计时边界同步，或使用 CUDA events。全设备 `torch.cuda.synchronize()` 会等待该设备所有 stream，共享并发服务不能把这一段等待全部算给某个用户。[PyTorch CUDA semantics](https://docs.pytorch.org/docs/main/notes/cuda.html)

NVML 的 process accounting 按进程生命周期统计，运行中的 `time` 有其限制；常驻服务一个 PID 包含很多请求，不能靠 PID 得到每个请求的账单。[NVML process accounting](https://docs.nvidia.com/deploy/archive/R550/nvml-api/group__nvmlAccountingStats.html) DCGM 的 Job start/stop 可帮助观察某设备组在区间内的情况，但它不替应用执行 OS 隔离；重叠 Job 也不会因此获得唯一归属。[DCGM Job stats 与隔离说明](https://docs.nvidia.com/datacenter/dcgm/latest/user-guide/feature-overview.html)

### 配额与硬限制不同

- 装饰器可以做准入检查、申请预占、记录时间、协作取消；不能限制绕过装饰器的 GPU 调用，也不能给共享 GPU 任意切出一个受硬隔离的份额。
- CPU 的 `--cpus` / `cpu.max` 限制运行时带宽；每日累计 CPU 额度还需要中央账本与外部监督者。[Docker 资源约束](https://docs.docker.com/engine/containers/resource_constraints/)
- GPU 硬 deadline 需要可独立停止的 Job 进程/容器与可信 host supervisor。取消 Python await 不等于已停止 CUDA kernel；常驻多请求进程的强杀会影响其他用户，只能声明软限制或采用可隔离执行结构。
- 采样和停止确认存在延迟；执行授权需预留停止余量并明确可接受误差，记录实际占用。简单 supervisor 不保证在预算最后一毫秒精确停止；无法约束停止能力的服务不得宣称硬限制。
- **RTX A6000 不应按支持 MIG 设计。** NVIDIA MIG 支持产品表列出 A100/A30 等及较新的 RTX PRO Blackwell，但没有 RTX A6000；A6000 的 vGPU 分类是 time-sliced。不要把 A6000 与 RTX PRO 6000 Blackwell 混同。[MIG 支持列表](https://docs.nvidia.com/datacenter/tesla/mig-user-guide/supported-gpus.html)、[vGPU 产品模式表](https://docs.nvidia.com/vgpu/latest/pdf/grid-vgpu-user-guide.pdf)
- A6000 可以从 NVML/DCGM 的可用监控能力开始，不能承诺所有 profiling counter 可用；DCGM 具体字段支持需按卡、驱动和权限确认。[DCGM profiling 支持](https://docs.nvidia.com/datacenter/dcgm/latest/learn/modules/profiling.html)
- 多个模型服务使用同一张 A6000 时，需要按真实 device UUID 的中央分配 lease；每个模型各自的 semaphore 会把同一张卡重复分配。宣称独占还要求覆盖该卡所有可访问进程；不受平台管理的旧服务同样可能竞争 GPU。

## 5. 最小 SDK 对接草案

以下是**尚未实现的伪接口**，`pskit_compute` 不代表已有包，`compute_tool` 不是 MCP 标准装饰器。身份来自中央签发的短期 execution grant，不能来自函数参数里随意填写的用户 ID。

```python
from pskit_compute import ComputeService, ExecutionContext, Artifact

service = ComputeService(service_id="lab-rna", model_version="sha256:...")

@service.compute_tool(
    name="predict_structure",
    execution="adaptive",                 # quick completed / durable pending
    resources={"gpu_count": 1, "min_vram_mb": 24000},
    accounting="allocated_device_seconds",
    isolation="exclusive_process",        # 必须是真实 executor 能力
    max_execution_seconds=1800,
)
def predict(sequence: str, ctx: ExecutionContext) -> list[Artifact]:
    ctx.check_cancelled()
    ctx.report_progress(phase="inference", completed=0, total=1)
    result = existing_model.predict(sequence)
    return [ctx.save_artifact(result, name="structure.cif")]

# 启动一次：可挂载 MCP/HTTP，也可由持久 receiver 领任务调用。
```

SDK 外壳要保留原函数签名和类型，自动派生输入输出 Schema；execution context 由注入层提供。函数报错时也要进入终态结算，不能只在成功返回时上报用量。`max_execution_seconds` 配置只有在 supervisor 真正执行 deadline 时才可宣称硬限制。

### 推荐的 PSKit 私有接口与事件

下面全部是**平台自定义契约**。名称仅作对接建议；当前 AF3 `/internal/compute/af3/...` 不是这些通用接口。

| 接口草案 | 用途 |
| --- | --- |
| `POST /api/v1/compute/jobs` | 用户或工具页面提交；后端绑定身份、做配额预占，返回 Job |
| `GET /api/v1/compute/jobs/{job_id}` | 按 ownership 查状态与 artifact |
| `POST /api/v1/compute/jobs/{job_id}/cancel` | 持久取消意图；收到停止确认前不释放仍在消耗的配额 |
| `POST /internal/compute/jobs/claim` | 受信任 worker 领任务和签名 grant；不暴露给用户沙箱 |
| `POST /internal/compute/jobs/{job_id}/heartbeat` | lease、进度及累计用量；必要时请求增加预占 |
| `POST /internal/compute/jobs/{job_id}/result` | 提交终态、最终累计用量、artifact；事务提交后 ACK |
| `POST /api/v1/admin/compute/jobs/{job_id}/reconcile` | 有原因和审计的用量纠正 |

```json
{
  "schema_version": "pskit.compute.v1",
  "event_id": "evt_...",
  "job_id": "job_...",
  "attempt": 1,
  "seq": 12,
  "service_id": "lab-rna",
  "model_version": "sha256:...",
  "phase": "inference",
  "status": "running",
  "cumulative_usage": {
    "wall_ms": 90000,
    "gpu_device_ms": 60000,
    "cpu_core_ms": 140000,
    "gpu_memory_peak_bytes": 24000000000
  },
  "measurement": {"policy": "allocated_device_seconds.v1", "source": "trusted_worker"}
}
```

后端保存关联 `user_id / tool_run_id? / project_id? / session_id? / run_id? / tool_call_id? / job_id / attempt`。其中 `run_id` 指 AgentRun，可选；普通工具页面只保留 ToolRun/Job 与真实用户，不为计账强行创建 AgentRun 或聊天 Session。确实调用 Pi 分析结果时，再创建或关联 AgentRun。`event_id + attempt + seq` 支持重复上报与重放；lease token/签名 grant 放鉴权信封，不回显到 Agent 或公开 artifact。

自定义统一业务结果：

```text
Completed(job_id, output, artifacts, usage_receipt_id)
Pending(job_id, poll_after_ms, resume_policy)
Failed(job_id, code, retryable, usage_receipt_id)
```

旧世代 MCP 可将它放 `structuredContent`；`Pending` 是已完成的“提交任务”工具结果，不能声称原 MCP tool call 仍未返回。支持新版 Tasks 时映射官方 task handle；不要将自定义 `status: pending` 冒充官方 `resultType: task`。失败、取消和超时同样可能已经使用计算资源，不能一律退款。

## 6. 配额生命周期与可靠恢复

推荐沿用单 PostgreSQL，用事务与 outbox 处理：

```text
reserve → claim → heartbeat → increase-or-stop → settle → reconcile
```

1. **reserve**：验证可信身份、模型可见性、并发与资源要求；事务锁定额度，预占下一执行窗口。保留 reservation ID、period、policy version。
2. **claim**：签发 job/attempt/worker/model scope 的短期 grant，包括用量上限、授权截止与固定绝对执行 deadline。重试和 heartbeat 不能重置绝对 deadline。
3. **heartbeat**：累计量单调增长，以单调时钟记录本机时间；中央按 event sequence 仅计新增部分。保存用量证据后 ACK。
4. **increase-or-stop**：将耗尽已预占窗口时先申请扩容，额度不足则发停止意图；worker 不在没有授权的窗口继续执行。网络断开只能在已授权预算和截止内继续，不无限放行。
5. **settle**：模型结束后，用最终累计值消耗 reservation，释放剩余预占，事务记录账单、Job 终态与 outbox。ACK 包含 `receipt_id`、`accepted_seq`、终态。
6. **reconcile**：worker 崩溃或主机断电时，Job 进入用量未知/待核对，不能直接按 0 结算。可信采样、持久 journal、晚到报告或管理员有原因修正，形成审计链。

每日重置不应让昨日仍运行的 reservation 消失。建议按 UTC 日对执行区间切片，旧日结算归旧桶，跨日续跑申请新日预占；全局并发仍约束跨日 Job。如果采用“提交日计费”，也必须公开口径、保留未结算预占，并设置固定 deadline，不能用日切换延长授权。

receiver 的 journal 和结果 outbox 先持久化、再上报；仅在中央**事务成功并返回匹配 receipt** 后删除本地已确认的记录。丢 ACK 重传同一 event/result，返回原 receipt；相同 job/attempt/seq 内容冲突则拒绝。主机重启恢复时不得把已有 GPU 输入再执行一遍。

后台 Job 完成后，outbox 触发同一 Run 的幂等唤醒。Pi 收到平台生成的结果事件/恢复指令，而不是伪装的新 User 消息；同时刷新前端事件。这个唤醒与账本恢复能力由 PSKit 提供，MCP 本身不会替平台唤醒 Pi。

## 7. 与 `new_backend` 现状的对接差距

以下只依据本地源码，不代表重新验证了线上部署。

| 现有事实 | 可保留 / 需要补充 |
| --- | --- |
| `app/adapters/live/remote_mcp.py`：Streamable HTTP、allowlist、Schema 检查、默认 30 秒调用、结果大小限制 | 保留准入与防护；暂无 task 生命周期、progress 投影、每用户 GPU/CPU 用量上下文 |
| `multi_remote_mcp.py`：多服务工具命名空间；`limited_mcp.py`：本地 semaphore + 可共享 lease | 保留；并发限制不等于每天计算预算 |
| `contracts/capabilities.py`：`McpInvokeResult.status` 仅 `completed` | 需要统一 completed/pending/failed，避免全部远程模型被短请求超时截断 |
| `api/internal.py`：Pi run token、tool call 幂等与运行态校验；公开 MCP 入口校验用户工具权限 | 保留可信身份与幂等入口；把执行上下文贯穿 adapter，不让模型提交身份字段 |
| `domain/persistent_conversation/af3.py`：AF3 预占、claim fencing、deadline、结算、late report/reconciliation | 泛化为所有计算服务；不要另做第二套预算真相源 |
| `domain/persistent_conversation/usage.py`：GPU 用量按 Job 创建日汇总；现有额度是整数分钟 | 当前不是跨日执行切片计量；新增通用账本应明确跨日 reservation 与执行中扩容，不能把提交估算视为已执行硬上限 |
| `scripts/af3_receiver.py`：SQLite WAL journal、独立 compute loop、本地 spool；终态 ACK 后删除记录 | 复用可靠恢复思路；目前最终用量是 subprocess 整段 walltime 向上取整分钟，包含 pipeline/load，未观测实际 CUDA 时间或 CPU core-seconds |
| `services/agent.py`：已有 AF3 完成后的 Run 唤醒 | 将 AF3 专用条件推广到统一 Job 完成事件 |
| `pi/extension.js`：通用 MCP 工具只返回结果；AF3 专用工具返回 pending 时带 `terminate`，resume 命令也绑定 AF3 | 通用 `Pending → 停止本轮 → 持久完成事件 → Pi 唤醒` 目前未贯通；不是只扩充结果 Schema 就能完成 |
| `api/admin.py`：用户 Token/GPU 限额、AF3 用量审计、Skill 管理 | 可作为管理页 API 起点；暂无通用计算服务 manifest、CPU 配额、共享服务分摊策略 |

源码入口：[依赖声明](/home/jhli/pskit-2.0/new_backend/pyproject.toml)、[远程 MCP](/home/jhli/pskit-2.0/new_backend/app/adapters/live/remote_mcp.py)、[结果契约](/home/jhli/pskit-2.0/new_backend/app/contracts/capabilities.py)、[AF3 账本](/home/jhli/pskit-2.0/new_backend/app/domain/persistent_conversation/af3.py)、[receiver](/home/jhli/pskit-2.0/new_backend/scripts/af3_receiver.py)、[Agent 唤醒](/home/jhli/pskit-2.0/new_backend/app/services/agent.py)、[Pi 扩展](/home/jhli/pskit-2.0/new_backend/pi/extension.js)、[管理 API](/home/jhli/pskit-2.0/new_backend/app/api/admin.py)。

## 8. 同门需要提交的最小对接资料

- 服务负责人、稳定 `service_id`、模型 ID/版本/许可证与权重版本。
- Python 函数签名或 HTTP OpenAPI；输入/输出 Schema；文件输入用 artifact 引用。
- GPU 型号/张数/最低显存、CPU/内存、是否常驻、是否允许请求并发。
- 预计时间、最大执行时间、取消语义、失败是否有部分结果、幂等键支持。
- 可信计量位置、计费阶段、可独立终止的 executor；共享进程明确声明仅软限制或分摊计费。
- `submit/status/cancel/result` 映射或主动领任务方式；artifact 上传与终态 ACK/replay。

拿到这些资料后，可以先产出每个服务的 manifest 和 adapter 映射表供同门审核。当前不需要他们修改成统一深度学习框架，不要求把所有模型塞入用户沙箱，也不以“包一个计时装饰器”宣称已解决硬配额与共享算力隔离。
