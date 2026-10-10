# PSKit 2.0：代码、模型兼容、部署与目录架构整改任务书

> **交付对象：Codex / Coding Agent**  
> **仓库：** `Alakazamc/pskit-2.0`  
> **目标分支：** `codex/new-stack-baseline`  
> **本次审查固定基线：** `5d703951d5ed50302e59c1f91e181ada05af81bd`（2026-10-10）  
> **状态：** 实施规格 / 待在本地仓库复核；本文**不是已执行的代码修改报告**。

## 0. 给 Codex 的执行指令（先读）

你是负责 PSKit 2.0 的资深 Python、React 与平台工程师。请基于**实际仓库代码**分阶段整改本文所列问题。目标不是增加功能，而是降低部署和代码复杂度、修复已定位缺陷、提高长期扩展能力，特别是支持多个 **OpenAI-compatible Chat Completions API**，同时**保留现有 PinCC + LiteLLM 的可用链路**。

**执行规则：**

1. **先审计再修改。** 首先确认当前 HEAD 和工作区状态；若 HEAD 已晚于本文基线，逐项核实是否已修复，避免重复开发。输出 `CURRENT_STATE_AUDIT.md`，逐项标记 `confirmed / already_fixed / not_reproduced / needs_live_validation`。本文中属于建议而非已复现的项，必须先证明问题或给出基准数据。
2. **禁止擅自操作生产。** 不得 SSH 发布、重启云端容器、改线上 Nginx / 供应商配置、迁移真实数据库、删除或覆盖数据卷、轮换密钥。涉及生产的步骤只能生成脚本与操作手册，等待明确授权。不得把真实密钥、Cookie、JWT、Pi transcript、用户输入内容写入报告。
3. **保留对外兼容性。** `/api/v1`、现有 SSE/Run 事件契约、`Pending → terminate → 自动唤醒原 Pi Session`、用户归属、quota/账本、MCP Receiver、Tool Product、OpenSandbox 文件与命令工具、AF3 等，除明确的版本化迁移外必须保持语义不变。
4. **不要“一次性大搬家”。** 目录重构和功能修改应分开提交；目录迁移以新 CI、发布入口、回滚路径、真实 import / Docker build 验证为前置条件。不以文件数量少为目标删除安全预检、账本恢复、迁移或隔离机制。
5. **只用必要依赖。** 保留 Python FastAPI + Pi RPC + React/Vite + LiteLLM + PostgreSQL；不主动引入 Next.js、LangGraph、Redis/Celery、Kubernetes、新的模型代理微服务或另一套状态数据库。只有真实性能/可靠性证据需要时才论证增加组件。
6. **明确提交边界。** 分阶段 PR/commit，给出对应回归测试、风险、回滚方式和迁移说明。任何需要变更公开请求/响应的行为必须先改契约、后端与前端测试。
7. **保持当前 Model Provider 工作。** 现有 PinCC 特殊补丁不可作为普通 OpenAI-compatible 服务的默认规则；真实供应商资格验收要验证结构化 `tool_calls`、工具结果续聊和流式分块，而不只是 HTTP 200。

---

## 1. 现状与目标

### 1.1 现状（从仓库代码确认）

- 旧栈 `backend/`、`frontend/` 与新栈 `new_backend/`、`new_frontend/` 并存；`.github/workflows/ci.yml` 仍以旧栈为测试、构建入口。
- `deploy/agent/` 有大量部署文件；Compose 组合涉及阿里云、Staging、本地、A6000 Receiver、Supabase、LiteLLM、OpenSandbox、gVisor 和配额卷插件。
- FastAPI 的 `AgentService` 同时管理 Pi 执行、队列、租约、事件投影、续跑与上下文；`CatalogStore` 同时管理 Skills、MCP 与文件。
- 模型走 **Pi → FastAPI 内部 Chat Completions 代理 → LiteLLM / generic gateway → 上游**；Pi 的 `models.json` 当前硬编码 `api="openai-completions"`。
- `ModelCatalog` 的公开能力主要是 `supports_images` 和 `reasoning_levels`；对于普通 OpenAI-compatible API，`/model/info` 往往不存在，不能自动推断真实能力。
- `infra/litellm/provider_compat.py` 目前仅解决 LiteLLM 1.100.3 与指定 PinCC Anthropic 路线在 `tools[*].type=custom` 上的兼容问题；该修复已有针对性离线与真实模型发布记录。
- PostgreSQL 正式存储已投入使用，但部分 Repository 仍经 `PostgresStatements` 用 SQL 字符串替换兼容 SQLite 语法。
- OpenSandbox/gVisor 提供隔离执行与持久卷，Server 挂载 Docker socket；执行控制面属于高权限系统，不宜同一般不可信执行逻辑混为一谈。

### 1.2 目标

**运维：** 日常更新 Web/API 不影响 Supabase、LiteLLM 数据库和长期计算任务；统一 `doctor / release / status / rollback` 入口，提供可审计、可回滚的版本清单。  
**模型：** 新增一个标准 OpenAI-compatible Chat Completions 供应商，原则上只需要配置、测试、审核、发布，不要求新增 Python provider if-else。  
**Agent：** 可恢复、幂等、可观测；在并发增加时不会因 50ms 轮询/逐 Token DB 写入拖垮服务。  
**前端：** SSE 断线恢复、流式重试和文件进度正确；模型/推理选项来自服务端权威能力契约。  
**代码：** 单体应用内模块化而非微服务化；职责清晰，目录和部署脚本不再靠积累 legacy overlays 维持。

### 1.3 非目标

- 不重写 Pi、不把 Python Backend 改成 Node、不迁移到 Next.js。
- 不开发新的通用大模型网关来取代 LiteLLM。
- 不修改、删除或公开模型维护者的私有服务凭据；计算服务可继续通过 HTTP/MCP/Receiver 与标准 `ExecutionReport` 接入，无需发布公共 PyPI SDK。
- 不为“减少 YAML 文件数”而牺牲多环境隔离、密钥隔离、计算账本一致性和回滚证据。

---

## 2. 优先级总览

| ID | 优先级 | 位置 / 已知问题 | 改造方向 |
|---|---|---|---|
| D-01 | **P0** | `deploy_to_cloud.sh` 调用 `stack.sh down`，会同时停止 Agent、LiteLLM、Supabase | 区分 bootstrap 与 app-only release；禁止日常发布停库 |
| D-02 | **P0** | `deploy_to_cloud.sh` 检查 `/api/v1/health`，而正式 API 是 `/health/live`、`/health/ready`；失败仍可 warning 后结束 | 正确 health + 版本检查 + 强制失败回退 |
| D-03 | **P0** | 部署脚本找不到 Turnstile key 时回退到公开测试 key，任务准入检查尚为人工占位 | 生产缺配置 fail closed；活动 Run/Job/Workspace 自动预检 |
| CI-01 | **P0** | `.github/workflows/ci.yml` 仍面向旧 `backend/ frontend/` 与根 Dockerfile | 加新版 Python 3.12/Postgres17/React/Pi/LiteLLM 专属 CI 门禁 |
| S-01 | **P0** | `host-nginx-agent-aliyun.conf` 中 `limit_req_dry_run on`；Backend auth abuse 支持 `observe` | 验证线上防护层，确保生产至少一层执行硬限流 |
| F-01 | **P0** | `new_frontend/src/features/chat/events.ts` 的跨批次 `run.retrying.reset_message` 不能清除先前 `previous.text` | 修复 reducer 并加跨批次回归 |
| F-02 | **P1** | SSE 非终态错误后约 200ms 重连；解析器对跨 chunk CRLF 不够稳健 | 共享有界 SSE parser；退避/jitter/Retry-After/终态分类 |
| M-01 | **P1** | `ModelCatalog` 仅画像/推理元数据；缺 Tool Calling 真实验收 | 现有 Model Policy 上扩展 `ModelRuntimeProfile` + Qualification |
| M-02 | **P1** | PinCC 修复依赖固定 LiteLLM 版本与内部 callback 时序 | 保留局部补丁，独立兼容测试、版本门禁与退役流程 |
| M-03 | **P1** | Pi 固定 `openai-completions`，供应商 reasoning/参数差异不集中 | `PiModelConfigBuilder` + 模型协议字段；先支持 Chat Completions |
| M-04 | **P1** | FastAPI 代理在生产默认每次创建 httpx.AsyncClient；会话命名自行构造模型 HTTP | lifespan 共享连接池 + 统一模型客户端边界 |
| M-05 | **P1** | Provider 错误按字符串关键字识别；`drop_params: true` 可能掩盖能力缺失 | 结构化错误归一化与 Tool/Usage/Stream 验收 |
| A-01 | **P1** | 50ms Agent Scheduler，`claim_queued_runs` / `claim_wakeups` 先取大量候选；同步 DB 操作在 async 事件路径 | 有界领取、指数退避、按事件/时间批量持久化文本 |
| A-02 | **P1** | `AgentService` 783 行，`CatalogStore` 接近 1000 行；Session 锁字典持续积累 | 按 Agent Scheduler/Executor/Recovery 和 Skill/File/MCP 拆分，管理锁生命周期 |
| DB-01 | **P1** | `PostgresStatements` 字符串替换 SQLite SQL；`_run` 物化全部结果 | 逐步原生 PG Repository；有界查询；严格事务与锁测试 |
| C-01 | **P1** | Pi 只看到泛化 `submit_compute(capability_id,version,arguments,budget)` | Capability 对 Agent 直接表现为业务 Tool，预算/版本由服务端绑定 |
| X-01 | **P1** | OpenSandbox Server 挂 Docker socket、同主机承载大量高权限基础设施 | 明确控制面权限/网络边界；中期分离独立执行节点 |
| R-01 | **P1** | 根目录双栈 + 部署 Compose/说明文档多入口 | 分阶段归档旧栈，按领域/运行角色重构目录、统一运维入口 |
| F-03 | **P2** | 模型选择器通过 `/` 解析供应商；全局 CSS 多套 Token；假进度；长历史全量渲染 | 服务端显式显示字段、CSS Token、真实上传进度/不定态、历史分页 |
| A-03 | **P2** | Pi Session File 为本地路径 + PostgreSQL 引用 | 明确单主机持久化边界；HA 阶段再引入共享 checkpoint store |

**注意：** P0 表中源码事实已定位，但不等于生产现状已验证；例如 Nginx 若另有边缘硬限流，需以 `nginx -T`、实际代理拓扑与生产有效配置为准。P1/P2 中的性能影响须提供基准数据，不能一概声称线上已出故障。

---

## 3. 目标目录架构（建议最终状态，而非立即迁移）

目标是 **单仓库 + 模块化单体 + 可独立部署的受控计算服务**。建议使用 `apps/` 表示对外应用，`infra/` 表示第三方基础设施，`deploy/` 表示运维和发布。以下是规划结构：

```text
pskit-2.0/
├── AGENTS.md
├── README.md
├── pyproject.toml                # 可选：统一开发脚本；不要创建无用顶层 Python 项目
├── apps/
│   ├── api/                      # 由 new_backend/ 迁入；仍是一个 FastAPI 进程
│   │   ├── pyproject.toml
│   │   ├── requirements.lock
│   │   ├── app/
│   │   │   ├── bootstrap/        # create_app、依赖组装、lifespan
│   │   │   ├── api/
│   │   │   │   ├── public/       # 面向浏览器的 /api/v1
│   │   │   │   ├── admin/        # 管理员 API
│   │   │   │   └── internal/     # Pi、Receiver、沙箱间受信任 API
│   │   │   ├── auth/            # Auth、Session、CSRF、滥用控制
│   │   │   ├── models/          # Profile、Policy、Discovery、Qualification、Gateway
│   │   │   ├── agent/           # Pi、Scheduler、Context、Event、Resume
│   │   │   ├── skills/          # 版本、授权、指令与目录
│   │   │   ├── capabilities/    # Capability Registry / immutable release
│   │   │   ├── tool_products/   # 配置驱动 Tool 产品/工作流
│   │   │   ├── compute/         # Job、Lease、Usage、Artifact、Result Receipt
│   │   │   ├── workspace/       # 文件、Attempt、Sandbox/GVisor Provider
│   │   │   ├── storage/         # PG repos、transaction、migrations
│   │   │   ├── contracts/       # Pydantic DTO 与跨模块协议
│   │   │   └── shared/          # 错误、配置、日志、纯工具
│   │   ├── pi/                  # Pi extension + system prompt + package lock
│   │   ├── scripts/             # 运行相关辅助入口；非生产部署脚本
│   │   └── tests/               # unit / integration / contract
│   └── web/                      # 由 new_frontend/ 迁入
│       ├── package.json
│       ├── src/
│       │   ├── app/              # Router、全局 Provider
│       │   ├── features/
│       │   │   ├── auth/
│       │   │   ├── chat/
│       │   │   ├── models/
│       │   │   ├── skills/
│       │   │   ├── tools/
│       │   │   ├── compute/
│       │   │   ├── workspace/
│       │   │   ├── admin/
│       │   │   └── usage/
│       │   ├── shared/
│       │   │   ├── api/          # typed HTTP + SSE + query keys
│       │   │   ├── components/   # 可复用 UI primitives
│       │   │   ├── hooks/
│       │   │   ├── i18n/
│       │   │   └── styles/       # 统一 light/dark 语义 tokens
│       │   └── assets/
│       └── tests/                # unit、browser / E2E
├── packages/
│   └── compute_protocol/         # 可选，若确有两个独立 Python 安装环境共享协议
│                                  # 不要求公共 PyPI；可用私有 wheel/git 或 HTTP schema
├── contracts/                    # OpenAPI / Event / Compute 的发布版 Schema
├── infra/
│   ├── supabase/                 # 官方自托管配置；尽量不直接维护镜像源码
│   ├── litellm/                  # 网关配置、PinCC provider_compat、测试
│   └── opensandbox/             # 受信任控制面镜像补丁、gVisor、quota-volume
├── deploy/
│   ├── compose/
│   │   ├── base.yaml
│   │   ├── local.yaml
│   │   ├── staging.yaml
│   │   └── production.yaml
│   ├── components/
│   │   ├── opensandbox.yaml
│   │   ├── compute-receiver.yaml
│   │   └── af3-legacy-receiver.yaml
│   ├── nginx/
│   ├── config/                    # example/Schema，不存真实密钥
│   ├── commands/                  # pskitctl：doctor/status/release/rollback/logs
│   ├── migrations/                # 运维用数据切换与恢复工具
│   └── smoke/                     # 环境验收
├── docs/
│   ├── architecture/             # 单一现行架构说明
│   ├── runbooks/                 # 当前生产操作手册
│   ├── adr/                      # 重要决策记录
│   ├── releases/                 # 版本化发布与证据
│   └── archive/                  # 历史迁移/旧拓扑文档
├── tests/                         # 跨进程/跨服务端到端测试
└── legacy/                        # 只有完成新栈切换后才归档
    ├── backend/
    ├── frontend/
    └── deployment/
```

### 3.1 “同一业务只有一个主人”的代码边界

| 领域 | 唯一职责 / 权威数据 | 不应该做的事 |
|---|---|---|
| `models/` | 发布模型 Profile、供应商能力资格、用户授权、使用哪种 Pi 协议 | 不持有用户对话、GPU Job |
| `agent/` | Run 状态机、Pi 事件、恢复、context 选择 | 不直接实现供应商特有 HTTP body、不管理部署 yaml |
| `skills/` | 如何使用能力的知识/提示/版本授权 | 不执行 GPU 推理、不成为第二套调度器 |
| `capabilities/` | 可执行业务能力的 Schema、版本和权限 | 不决定 Tool Product 页面布局 |
| `tool_products/` | 给人使用的配置式工具 UI/产品工作流 | 不独自实现另一套额度与作业状态机 |
| `compute/` | Job、Lease、Usage、Receipt、Artifact 结算 | 不感知 React、模型提供商样式 |
| `workspace/` | 用户文件与受控沙箱命令执行 | 不持有管理员主密钥或数据库高权限 |
| `infra/litellm/` | 上游调用路由、账单/预算、非常有限的供应商协议修补 | 不决定用户 Skill、聊天和内部权限 |

### 3.2 旧路径 → 新路径的迁移映射

| 当前路径 | 拟迁目的 | 操作策略 |
|---|---|---|
| `new_backend/app/main.py` | `apps/api/app/bootstrap/` + thin entrypoint | 先从工厂中提取依赖初始化，最后再移动 |
| `new_backend/app/services/agent.py` | `apps/api/app/agent/{service,scheduler,pi_executor,event_projector,resume,context_builder}.py` | **先在原目录内拆分**，行为测试通过再移动 |
| `new_backend/app/domain/catalog.py` | `apps/api/app/{skills,workspace,capabilities}/` | 拆 Skill/File/MCP Catalog，保留薄兼容 facade |
| `new_backend/app/services/model_catalog.py`、`domain/admin/model_policy.py` | `apps/api/app/models/` | 在原有 Admin 权限/Revision 上扩展，不建第二套 Model Policy |
| `new_backend/app/api/internal.py` | `apps/api/app/api/internal/{models,mcp,compute,af3}.py` | 路由保持原 URL，拆 handler / service |
| `new_backend/app/db/postgres.py` 和 `domain/persistent_conversation/` | `apps/api/app/storage/` 与相应领域 Repository | 替换 SQL 兼容层需单独 PR 和 PG 回归 |
| `new_backend/pi/` | `apps/api/pi/` | 保留 `--no-builtin-tools`、Extension 和 resume 机制 |
| `new_frontend/src/features/mono/` | 对应各 `features/` | Mono 是视觉壳，不应再持有全站不同业务逻辑 |
| `new_frontend/src/api/` | `apps/web/src/shared/api/` | SSE parser 与 typed HTTP 边界单一化 |
| `new_frontend/src/styles/` | `apps/web/src/shared/styles/` + component CSS | 先整理 Token，再删层叠覆盖；不要一口气重写样式 |
| `infra/litellm/` | **保留路径** | 供应商兼容补丁和网关配置继续隔离 |
| `deploy/agent/*compose*` | `deploy/compose/` + `deploy/components/` | 先通过生成器编译出等价 resolved Compose，确认后切换 |
| `backend/`、`frontend/`、根 `Dockerfile` 等 | `legacy/` | 直到新 CI/构建/镜像/发布/回滚入口全部迁移且确认无人引用 |
| `docs/`、`deploy/agent/*.md` | `docs/runbooks/`、`docs/archive/` | “当前指南”只有一份；原历史验证文档不可删 |

**约束：** `apps/` 是一个**最终建议**。如当前发布期路径迁移风险过高，可以先保留 `new_backend/`、`new_frontend/` 两个目录，完成内部逻辑拆分与新 CI；在独立“纯路径迁移”PR 才整体迁入 `apps/`。不要同时做全局 `mv`、重写 API、替换 DB 层、更新模型协议。

---

## 4. 模型接入的目标架构（重点）

### 4.1 关键原则

1. **OpenAI-compatible ≠ OpenAI 同等能力。** `/v1/models` 返回一个名称，并不证明支持 Tool Calls、Tool Choice、Reasoning Effort、Images、Usage 或标准 SSE。
2. **Chat Completions 优先。** 已有 Pi `openai-completions` + FastAPI Proxy + LiteLLM 路径先支持标准 Chat Completions；`Responses API` 是第二阶段独立协议适配，而非将字符串替换为 `openai-responses` 就算完成。
3. **Model Runtime Profile 由服务端权威发布。** 前端只能展示；Agent 使用发布版本；管理员配置并测试，不能靠模型 ID 子串猜测供应商。
4. **双层准入。** 管理员发布 Model/Profile 能力不意味着用户授权；需继续遵守 Model Policy 的用户/群组允许列表、额度/成本上限，以及每次模型调用时的授权检查。
5. **兼容只作用于指定 Provider/Endpoint。** PinCC Anthropic Native patch 不得影响一般 OpenAI-compatible provider。`drop_params: true` 不能替代质量验收。

### 4.2 改造后调用图

```text
React ModelPicker ── /api/v1/models ──► ModelPolicy / ModelRuntimeProfile
                                          │  immutable qualified version
User message ────────────────────────────►│
                                          ▼
                                     Agent Run
                                          ▼
                                  PiModelConfigBuilder
                                          ▼
                                   Pi RPC model call
                                          ▼
                            FastAPI Internal Model Proxy
                           Auth + Quota + Request Policy
                                          ▼
                              Shared Gateway HTTP Client
                                          ▼
                                LiteLLM (existing)
                                     │       │
                          generic OpenAI   Anthropic Native
                           ChatCompletion   PinCC-specific patch
                                     └──► Upstream provider
```

### 4.3 建议内部契约（说明性草案，不是当前线上数据库 Schema）

```python
from typing import Literal
from pydantic import BaseModel, Field

Reasoning = Literal["off", "minimal", "low", "medium", "high", "xhigh", "max"]

class ModelRuntimeProfile(BaseModel):
    alias: str                       # 对浏览器稳定可见的名称
    revision: int
    provider_label: str             # UI 显示，不从 alias 的 / 推断
    display_name: str
    pi_api: Literal["openai-completions", "openai-responses"] = "openai-completions"
    supports_tools: bool = False
    supports_tool_streaming: bool = False
    supports_images: bool = False
    reasoning_levels: list[Reasoning] = Field(default_factory=list)
    context_window: int | None = None
    max_output_tokens: int | None = None
    qualification_status: Literal["untested", "passed", "failed", "degraded"] = "untested"
    qualification_evidence_id: str | None = None

class ModelWirePolicy(BaseModel):
    chat_max_tokens_field: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    stream_usage_mode: Literal["supported", "not_supported", "unknown"] = "unknown"
    developer_role_mode: Literal["supported", "system_fallback", "unknown"] = "unknown"
    reasoning_mapping: dict[Reasoning, str] = Field(default_factory=dict)
    # 只表示当前 Chat Completions / Pi API 的受控映射，不是全供应商统一转换器
```

**建模注意：**

- 不要将“某供应商支持工具”与“某个别名的所有部署都支持工具”混为一谈。多 deployment 同一 alias 必须具有相同最低能力/相同协议；否则拆 alias，或使用有能力约束的显式路由。
- 对未知能力继续 fail closed，但必须提供管理员**受控人工声明 + 真实验证**途径；普通 `/v1/models` 通常不包含 LiteLLM 的 `model_info`。
- `ModelDraft` 中现有权限、发布与修订机制尽量复用；**不要另建平行的 Model Registry 权限数据库**。Profile revision 在 Run 创建时形成可信快照；新的模型调用仍要复核是否已撤销授权。
- 逻辑模型名称与实际 supplier model ID、协议路径和密钥是不同概念。用户不应看到 provider key，也不能自己提交网关 endpoint URL。

### 4.4 模型资格验收（Qualify Model）

引入受保护管理命令/API：`Qualify` → 保存证据 → 审核 → 发布。资格验证按所宣称功能逐项执行，并对模型 alias、具体 deployment/路由、LiteLLM 版本、Provider URL 指纹、时间、请求/响应指纹、结果保存审计证据；**不要保存真实私密 prompt 或 API key**。

**最低验收矩阵**：

| Case | 必须检查 | 通过标准 |
|---|---|---|
| Text | 非流式普通消息 | `choices[].message.content` 可解析 |
| Stream | 流式文字、chunk 拆分、结束帧 | 增量拼接、finish_reason、断线行为符合契约 |
| Tools | 一个真实 function tool call | 结构化 `tool_calls`；函数名、arguments JSON 有效 |
| Tool Continue | 提交工具结果继续一次模型调用 | 模型正确收到 `role=tool` 和关联 tool_call_id |
| Streaming Tools | 分片的 id/name/arguments | 准确聚合，不能将普通 `<write_file>` 文本当作执行 |
| Usage | 非流/流用量字段缺失或不同 | 可用时记录供应商 usage，未知保持 unknown，不虚构零 |
| Images | base64/data URL 图片调用 | 确认上游实际接收并完成回答 |
| Reasoning | 每个声明可用档位 | 请求参数被上游接受且配置映射与证据一致 |
| Errors | 401/403/429/5xx/余额不足/超时 | 结构化错误映射、预占与退款安全、前端提示一致 |
| Alias routing | 同 alias 下多 deployment | 被路由到的任何 deployment 均满足公布能力 |

若供应商不支持强制 `tool_choice`，可使用受控自动选择测试，但未出现真正结构化 `tool_calls` 时**不能通过 Agent Ready**。不得通过提示词伪造工具执行。

### 4.5 PinCC 兼容处理

当前 `infra/litellm/provider_compat.py` 已是**目标主机 + Anthropic Provider + 固定 LiteLLM 版本**的非常有限修补，应保留，禁止全局泛化。改进方向：

- 增加 `ProviderCompatibilityCase` 测试矩阵和版本兼容说明，仍运行已有 `test_provider_compat.py`；适当把它纳入新版 CI 的固定容器任务。
- 在升级 LiteLLM 的候选环境验证内部回调触发位置、同步/异步、stream/non-stream、真实 `tool_use` 往返；不兼容则拒绝升级。
- 提供独立的“修复仍需要吗”检测，以便未来上游/新 LiteLLM 原生修复后移除补丁。
- 保证不改官方 Anthropic 和任何 `openai/*` 新供应商，不改缓存标记、工具 Schema、tool_choice 或工具结果。
- 不允许根据模型文本推断并执行 Shell/Write 指令。

### 4.6 新增 OpenAI-compatible Chat Completions Provider 的**最小路径**

先支持通用供应商，不改 Pi 的现有业务 Tool。示意（假设供应商提供标准 `/v1/chat/completions`）：

```yaml
model_list:
  - model_name: lab-research-v1
    litellm_params:
      model: openai/research-model-v1
      api_base: https://llm.example.com/v1
      api_key: os.environ/LAB_RESEARCH_API_KEY
```

这里是**说明格式**，生产模型可能由现有 LiteLLM Admin UI/数据库管理，无需强制用 YAML 定义。API key 只在受控服务器存放，并配最小模型权限和费用上限。

操作路径：

1. 在 LiteLLM 的现有配置/管理渠道添加独立模型 alias，确认 `/v1/models` 真实可见、认证和常规聊天可用。
2. 用 `ModelQualification` 测试流式、`tool_calls`、`role=tool` 续聊、usage、reasoning、images、429/超时；**能力有差异就记录，不强制所有模型具备所有能力**。
3. 在 PSKit 管理台发布 `ModelRuntimeProfile` 并配置用户/组 allowlist；需要 Tool 的 Agent 运行只选 Agent Ready 模型。
4. Frontend 读取统一 `/api/v1/models` DTO，不靠 `/` 猜供应商，不曝光 secret/endpoint。
5. 在 Staging 用真实 Pi + 本地隔离文件工具完成 `write_file → read_file → artifact.created` 端到端测试；核对用户 quota/模型账单只扣一次，上传产物所有权正确。
6. 经过人工批准后才灰度生产，保留旧 PinCC 路线作为回退选项。

**暂缓范围**：Responses-only Provider。准备好单独的 `ModelTransport` 适配界面即可；等有明确供应商才增加 `/internal/model/v1/responses`、相关 SSE、Usage 和 Pi API 配置与端到端用例。不要为了“未来可能用”先实现一套未经测试的协议转换。

---

## 5. 需要直接修复的 Backend 正确性与性能问题

### 5.1 Agent Scheduler / PostgreSQL

相关位置：

- `new_backend/app/services/agent.py`：`_scheduler_loop()` 当前约 50ms 一轮、同步调用多个 Store 方法。
- `new_backend/app/domain/persistent_conversation/runs.py`：`claim_queued_runs()` 先查全部候选，再 `limit` 条逐个尝试。
- `new_backend/app/domain/persistent_conversation/recovery.py`：`claim_wakeups()` 同样先读大量候选再在事务中领取。
- `new_backend/app/services/agent.py`：`text_delta` 直接写 `agent_events`。

要求：

- 在数据库端做 bounded candidate query，保证公平性、独立用户/会话并发限制与正确 lease/fencing；优先现有 PostgreSQL 事务/锁，验证是否应使用 `FOR UPDATE SKIP LOCKED`，不能跳过使用中的 Run，也不能双领取。
- 空闲调度使用退避，有新 Run 时快速唤醒；可以先实现轻量 event signal + fallback poll，不要求立即引入 Redis。
- 对非关键 `message.delta` 做有界时间/字节合并与批量持久化。`tool.started`、`tool.finished`、approval、Job、Artifact、终态和结算事件要有可靠顺序与提交语义。
- **注意同步 PG → 异步迁移陷阱**：不要把共享带 thread-local 事务语义的 `PostgresStatements` 直接零散塞进 `asyncio.to_thread`，必须保证连接/事务仍在同一执行上下文。
- 增加 benchmark：空闲 5 分钟请求数、100/1000 Run 排队的 DB 查询量、首 token 延迟、事件写入次数和队列公平性。用数据判断性能变化。

### 5.2 AgentService / Catalog 拆分

- 将 `AgentService` 的调度、Pi 执行、Run 事件投影、Resume 和上下文收集拆为小组件。初期保留 `AgentService` 对外 facade，不修改路由和依赖注入 API。
- `self._locks.setdefault(session_id, asyncio.Lock())` 当前字典无明显淘汰路径。实现引用计数/活跃 Run 范围的 Session Lock Registry，确保在仍有等待者时不移除活跃锁。
- `CatalogStore` 分出 `SkillRegistry`（版本、审核、授权）、`FileCatalog`（文件权限/内容）、`ToolCatalog`（MCP/Capability 发现），减少单类横跨权限、文件和 Skill 逻辑；权限仍以服务端为准。
- 长任务保留 `submit → Pending → terminate → 后台结果 → triggerTurn`、幂等调用 ID、持久 outbox 和 lease 恢复；不得把 Job 完成伪造成普通用户消息。
- `submit_compute` 不再让模型填写 `version/budget`：根据当前用户已发布 Capability Tool 的业务 Schema 动态注册工具，例如 `predict_binding(...)`；服务端根据不可变 release 生成预算/审批/Job，外部仍统一 `ExecutionReport`（Completed/Pending/Failed + 真实使用量）。模型自报/服务自报 usage 不能替代可信强制 quota。

### 5.3 PostgreSQL / Session

- `PostgresStatements.execute()` 目前将 `rowid` / `json_extract` / `?` 等字符串替换为 PG SQL；为原生 PG Repository 建立单向迁移策略，不再为新 SQL 扩展兼容转换规则。
- `QueryResult.fetchall()` 物化整个结果；Run、Events、模型目录、账本中的高频/大结果查询采用有界分页和显式索引检查。
- PostgreSQL 事务/锁/令牌结算的测试必须用真实 PG17，不只依赖 mock SQLite。
- Pi transcript 当前以本地 `session_file` 路径与 PostgreSQL 关联：明确单机/共享卷要求。未来多机时使用版本化 `SessionCheckpointStore`，保证写入顺序和持久一致性；**本轮不要求实现跨机 HA**。

### 5.4 Model Proxy/HTTP Client

- `new_backend/app/api/internal.py` 在未注入 `app.state.model_gateway_http_client` 时为每个请求创建 `httpx.AsyncClient`。将 GatewayClient 的 AsyncClient 放到 FastAPI lifespan 中，配置安全连接池、timeout 和代理策略，并在 shutdown 时关闭；流式响应期间保持 client/response 生命周期正确。
- 与 `session_titles.py` 共享同一受控模型请求适配层，但标题调用仍使用独立预算/用途策略；不得因此混淆 Token 账本和模型授权。
- model proxy 的每个出站请求绑定真实 Run owner，剥离客户端伪造身份；用户不能覆盖模型 ID、网关地址或提供商 key；响应错误返回脱敏稳定错误码。
- 当前 `_provider_error_code()` 靠错误文本匹配 `401/403/429/余额不足`，改为结构化 `status + provider_error.code + provider type` 分类。无法识别时保留 `UNKNOWN_UPSTREAM_ERROR`，不把 401/429 误判为 GPU quota。
- `model_gateway_kind`、`MODEL_GATEWAY_BASE_URL` 与旧 `NEW_API_*` 兼容变量可设置明确弃用流程；不要在同一 config 中无限积累别名。
- 需要时限制请求正文大小、总图片数、URL 图片策略、接收流大小、stream cancellation；不要让外部模型请求携带任意内部 URL 或泄露 auth header。

### 5.5 OpenSandbox / 安全边界

- 保留 gVisor + Docker 容器隔离；不能为了减少部署文件退回主机原生执行未信任命令。
- 当前 OpenSandbox Server 访问宿主 Docker socket，因此把它视为**高权限控制面**；审计 socket、沙箱网络隔离、volumes、用户身份、宿主 root 权限和被允许调用的内网服务。
- 中长期把 OpenSandbox / 高风险 user-code execution 放到独立可信执行节点，与 Supabase/LiteLLM/PostgreSQL 控制面隔离；但先保障现有 file/command/Artifact 路径可用，再开展拓扑迁移。
- 对 Workspace Command SSE 的用户取消、本地读流 Abort、远端进程取消 ACK 和最终未知状态增加测试；不能只发送 cancel 就认为进程已经退出。

---

## 6. 需要直接修复的 Frontend 问题

### 6.1 `run.retrying(reset_message=true)` 跨批次文本残留 — 必修

文件：`new_frontend/src/features/chat/events.ts`。

当前 reducer 在 `run.retrying` 分支重置的是本批次局部 `textDelta`，未重置 `previous.text`。因此：

```text
projectEvents(emptyRun, [message.delta("old")])       → text="old"
projectEvents(previous, [run.retrying(reset_message)]) → text 仍然是 "old"（错误）
projectEvents(previous, [message.delta("new")])        → text="oldnew"（错误）
```

按事件顺序设置 `reset_message` 状态并清空累积回复/错误，再应用后续增量；不要清空既有 plan、已真实完成的 tool 和 artifacts。新增跨三次调用与重放去重测试，检查 terminal/历史恢复行为。

### 6.2 SSE 连接与 Parser

文件：`new_frontend/src/api/http.ts`、`features/chat/useRunEvents.ts`、`features/chat/events.ts`。

- 抽成可复用且有界的 SSE Frame Decoder：处理 CRLF 恰好跨二进制 chunk、注释、空行、多行 data、UTF-8 跨 chunk、异常 JSON、最大 frame byte 限制。
- `401/403` 不无限重试；`404` 校验 Run 是否已经不可见；`429` 尊重 `Retry-After`；`502/503/网络错误` 指数退避 + 抖动 + 上限；Abort / terminal 不应产生假故障告警。
- 持久 cursor 恢复；对重连重复事件去重；`projectEvents` 不应为每一个新 token 遍历全部历史事件并无界保留完整数据。历史审计应由服务端按页读取，UI 使用增量可见状态。
- 避免把“立即轮询 50ms”作为首 token 性能唯一保证，使用可靠 SSE，并测量 P50/P95 TTFB/首增量延迟。

### 6.3 文件和消息 UX

- `Composer` 上传进度目前主要在请求完成时置为 100%；改成真实可观察进度或明确“不确定进度”，避免假百分比。
- Browser LocalStorage 草稿可包含敏感科研文本：加按用户/会话清理策略、退出清理选项与隐私说明；仍不得存 Token、blob URL 或真实文件字节。
- `ModelPicker` 不要通过拆 `alias.split('/')` 推断 provider；使用 `display_name`、`provider_label` 等服务端字段，且 tool/vision/reasoning 能力来自已发布 Profile。
- React 消息列表支持有界历史/分页与长对话性能；不要轻易用虚拟列表破坏滚动定位、展开折叠和 Artifact UI。
- `styles/base.css`、`mono.css`、`mono-pages.css`、`monochrome.css` 等目前有重复语义/主题变量；建立单一 light/dark semantic Token 来源，组件样式分模块迁移，保证现有紧凑黑白 UI 不回退为旧绿色方案。

---

## 7. 部署、配置与 CI 的整改设计

### 7.1 区分**首次基础设施初始化**与**日常应用发布**

现有 `deploy/agent/stack.sh up/down` 管三个 Compose 项目；`deploy_to_cloud.sh` 在应用升级时直接执行 `stack.sh down`，会中断 Supabase 和 LiteLLM。此路径不能继续作为日常发布流程。

建议建立单一运维入口（命令名称可协商，以下是期望 UX）：

```bash
pskitctl doctor --env prod
pskitctl status --env prod
pskitctl render --env prod       # 编译/检查 resolved Compose，不变更线上
pskitctl release --env prod --version <immutable-release>
pskitctl rollback --env prod --to <previous-release>
pskitctl logs --env prod --service backend
```

职责拆分：

| 命令 | 负责 | 不得做 |
|---|---|---|
| `bootstrap infra`（一次性） | Supabase、LiteLLM DB/用户、网络、卷、必要管理权限 | 自动运行在每次 app release |
| `migrate`（单独审批） | Schema 前向兼容迁移、备份/预检、审计 | 与普通 `release` 隐式绑死 |
| `release web` | 构建好的 Web assets 原子切换 | 停止/重启数据库和模型网关 |
| `release api` | 固定 SHA/镜像升级 backend，仅更新相关服务 | 停止 Supabase/LiteLLM/长 GPU 任务 |
| `release executor` | 受控 OpenSandbox/Receiver 灰度、证据/回退 | 直接覆盖正在运行的 Attempt/Job |
| `rollback` | 回退有状态边界之外的应用制品并验收 | 把数据库 schema 假定可自动降级 |

`release api` 用同一个明确的 Compose 项目、固定 overlay 清单与 `up -d --no-deps --wait backend` 等等价手段完成；是否无损滚动由当前副本/连接和状态支持程度决定，不得虚称零停机。

### 7.2 Release Manifest

每次发布至少记录：

```yaml
release_id: <unique-id>
source_commit: <full-sha>
api_image: <immutable-image-digest>
web_assets_sha256: <sha256>
database_schema_version: <version>
pi_version: <version>
litellm_version: <version>
provider_compat_hash: <sha256>
opensandbox_release: <digest-or-none>
compose_render_hash: <sha256>
checks:
  ci: passed
  preflight: passed
  smoke: passed
```

实际发布入口读取 Manifest，而不是在生产服务器 `git pull` 后现场随机构建。前端采用版本化 assets 目录 + 原子 `index.html` / symlink 切换；保留旧 hashed assets，以避免 `rsync --delete` 清理后导致打开中的旧页面引用失效。

### 7.3 生产 Preflight 必须 fail closed

- 校验 `.env`、私有文件权限、镜像 digest、Docker networks/volume、DB schema，禁止生产 Turnstile test key fallback。
- 数据库迁移仅在明确迁移命令执行，不作为每次 Agent 启动的隐式操作。
- 自动核对 `Run / Compute Job / Workspace Attempt / Receiver journal/outbox` 的未完成状态、活跃 lease 和未知状态。不能声称“没有任务”却只靠人工 yes/no。
- `health/ready`、认证鉴权 API、受控 Pi 模拟请求、SSE/Artifact、生产边界 Nginx `internal` 404；关键 smoke fail 则发布失败，不是 warning。
- 管理管理后台公网访问、防爬/限流：`host-nginx-agent-aliyun.conf` 当前 `limit_req_dry_run on`。部署前检查是否有有效的 ESA/Cloudflare/Nginx/Backend 强制限流；不要未经验证便直接删 dry-run 导致误封。
- 真实 client IP 必须只来自可信反代（源站不能绕过可信代理直接伪造）；如后续切换 ESA/Cloudflare，更新受信任的 IP 传递和反代链验证。
- `OpenSandbox Server` Docker socket 与私有 runtime network 独立审计，不允许 Browser/Agent 任意触达。

### 7.4 只保留一套“当前生产真相”

- 当前 `deploy/agent/` 有多种 cloud/staging/return/A6000/opensandbox 覆盖文件。**先解析实际组合**：列出环境、目标机器、Compose 顺序、引入的 service/network/volume、真实运行引用；生成矩阵。
- 给每个 YAML/脚本标 `active-prod / active-staging / active-dev / migration-only / rollback-only / obsolete-candidate`。不允许 Codex 看到相似文件就删。
- 形成 `base + environment + optional component` 少量稳定入口；其余专项迁移/历史手册归档。必须证明 `docker compose config` 等价或明确列出安全差异。
- 真实运维文件集中 `deploy/`，第三方 Supabase/LiteLLM 自身 compose 和固定配置保留 `infra/`，不强迫跨项目 Compose 合并。

### 7.5 新版 CI

当前 `.github/workflows/ci.yml` 使用旧 `backend/ frontend/`，需要引入针对新栈的明确流程：

- Python 3.12：ruff/compile、`new_backend` 单测、真实 PostgreSQL 17 集成、迁移向前兼容检查、并发/租约/幂等/配额测试。
- Node：`new_frontend` `npm ci / typecheck / lint / vitest / build`；Pi `extension.test.js`；OpenAPI/Event Schema 类型生成一致性检查。
- 模型协议：离线 Chat Completions 文字、stream、分片 `tool_calls`、tool_result 续聊、429/usage；PinCC 的 `test_provider_compat.py` 使用固定 LiteLLM 容器，验证官方 Anthropic 与 OpenAI 路由**不被误改**。
- 构建：新 `backend.Dockerfile` 与 Web dist；检查非 root 身份、健康检查、密钥不落镜像层和构建可重复性。`requirements.lock` 目前钉版本但不附哈希，可评估改用完整 lock + hashes 的可复现安装。
- Docker Compose：本地/Staging/Production 有效配置渲染；OpenSandbox/gVisor 专项 integration 可独立于每次轻量 PR 执行。
- CI 必须覆盖正在开发的分支/PR，而不只对旧 main 默认路径执行；和实际生产构建的相同入口对齐。

---

## 8. 分阶段实施顺序与提交边界

### Phase 0：现状审计与安全基线（只读）

**任务**：扫描导入关系、当前 active Compose、旧路径引用、真实模型路由、部署/CI 调用链；确认 2 节问题是否仍存在；收集最小基准。  
**产物**：`docs/architecture/CURRENT_STATE_AUDIT.md`、`DEPLOY_MATRIX.md`、`RISK_REGISTER.md`。  
**Gate**：明确哪些改动需要修改数据格式/在线部署；Codex 不执行生产变更。

### Phase 1：P0 行为与安全缺陷

**任务**：修跨批次文本重置；SSE 断线和解码器基础边界；部署脚本改为应用独立升级或明确禁用不安全旧脚本；修 health 路径和失败处理；取消生产 test-key fallback；确认 Nginx/应用实际限流执行层。  
**Gate**：跨批次 retry 测试；人工故障注入 401/429/503；静态证明 release 不触及 DB/LiteLLM 项目；无关键 smoke 时无法报告成功。

### Phase 2：让新栈测试成为发布门禁

**任务**：新 Backend/Postgres/Frontend/Pi/PinCC CI；输出固定镜像、合同一致性与 Release Manifest 雏形。  
**Gate**：干净工作区/固定依赖完整 CI green；代码产生的镜像/静态资源与测试对象一致。

### Phase 3：Model Profile + OpenAI-Compatible API

**任务**：复用现有 `ModelDraft`/`ModelPolicy`，新增 qualified Model Runtime Profile；服务端模型展示属性；共享 GatewayClient；动态 Pi Config Builder；对普通 OpenAI-compatible Chat Completion 路由完成单独的资格验收与灰度说明。  
**Gate**：PinCC 路线无回归；新 provider 在 Staging 真实完成 `tool_calls → tool_result → 工具写文件 → artifact`；错误、用量与 reasoning/image 不作无依据承诺；未验证模型不会进入 Agent Ready。

### Phase 4：Backend 运行时优化

**任务**：限制 DB 调度扫描、事件批处理、拆 AgentService/Catalog、锁字典生命周期、逐步迁移原生 PG Repository。  
**Gate**：保留 idempotency/lease/fencing、跨重启 wakeup、失败账本语义；给出 P50/P95 和 DB query/write 指标，不以主观感觉声称优化成功。

### Phase 5：Frontend UX 与能力组件

**任务**：模型展示字段、upload 真实进度、长期草稿清理策略、分页/消息滚动性能、CSS semantic Tokens。  
**Gate**：桌面/手机宽度及 dark/light 回归、可访问性、已有 Artifact/Mol* 与中英文渲染不退化。

### Phase 6：目录架构迁移（单独纯迁移 PR）

**任务**：确认各路径不被生产脚本、CI、Dockerfile、第三方工具引用；先完成业务模块分拆，再考虑 `new_*` → `apps/*`。独立提交修改 import、workdir、构建 context、测试、相对路径、PI Extension、镜像产物、文档与上线脚本。  
**Gate**：全套 CI/Compose/smoke green；git diff 能辨认大部分只是移动；PR 不带新的业务逻辑；已有发布方式/回退版本继续可执行。旧栈先搬 `legacy/` 而不是物理删除。

### Phase 7：云端发布整合与基础设施边界（受控）

**任务**：统一 `pskitctl`、Release Manifest/doctor、前端原子切换、OpenSandbox 与 Supabase/LiteLLM 隔离决策；完善备份与 RPO/RTO 实测方案。  
**Gate**：在 Staging 演练：仅更新 Frontend、仅更新 Backend、模型网关独立更新、沙箱失败关闭、真实 Pending 任务不断账、受控回滚；未经明确授权不得切生产。

---

## 9. 强制测试矩阵与防回归

| 场景 | 最低验收条件 |
|---|---|
| SSE 分批重试 | 旧文本不残留；计划、已完成工具、Artifact 不丢失 |
| SSE 异常 | CRLF/UTF-8 分块正确；401/403 停止盲重试；429 使用 Retry-After；503 退避并续接 cursor |
| 多 Session 并发 | 单 Session 不并行重复 Pi turn；不同 Session 可并发；Session Lock 可回收 |
| Durable Run | 崩溃重启后无重复外部副作用；Pending→自动恢复原会话且消息归属正确 |
| Compute/AF3 账本 | 失败/Pending/迟到报告与 unknown 用量不按 0 假结算；receipt/lease 幂等 |
| 模型别名 | 工具能力不因一个部署支持就错误推广到混合路由的全部部署 |
| 通用 OpenAI API | Chat Completions 文字/流/tool_calls/tool result/usage/错误/图片按发布能力通过 |
| PinCC | `type=custom` 仅指定 Native 请求受控修补；其他 Provider 原始工具定义不变 |
| OpenSandbox | 不可信文件/命令隔离有效，断线取消、Artifact 字节、用户边界、持久卷均验证 |
| 安全 | 跨站保护、匿名限速、Google 登录 state/PKCE、可信 IP、管理后台授权不回退 |
| 生产发布模拟 | API-only/Web-only 不停止 Supabase/LiteLLM；错误 readiness 阻止发布；可按 Manifest 回滚 |
| 目录迁移 | Python/TS imports、Docker contexts、CI、Compose、`pi/extension.js` 引用和测试全部绿 |

**测试证据必须可复现**：记录命令、测试环境/版本、输出、失败原因、耗时与需人工验收项。若真实供应商不可用，应明确 `not tested`，不能用 Mock 测试宣称真实 Tool Calling 已通过。

---

## 10. 每个阶段的 Codex 输出格式

每完成一个阶段，提交：

```text
CHANGE_SUMMARY.md
- 本阶段改了什么、为什么要改、对应表中哪些 ID
- 精确文件路径与主要符号 / API 变化
- 旧行为 → 新行为，对用户/运维有什么影响

VERIFICATION.md
- 运行命令与结果（passed / failed / skipped / not tested）
- 环境和依赖版本、重要截图/日志的脱敏摘要
- 尚不能证实的假设和性能基准

MIGRATION_AND_ROLLBACK.md
- 是否需要 DB/Env/Compose 迁移
- 如何在 Staging 演练、失败如何回退
- 线上执行是否需要授权（默认为需要）

下一阶段建议
- P0 blocker；技术债；新增发现是否超过原范围
```

PR 最好按 `fix(frontend-events)`、`fix(deploy-release)`、`test(new-stack-ci)`、`feat(model-qualification)`、`refactor(agent-runtime)`、`refactor(repo-layout)` 等单一职责划分，不要把所有修改压入一个巨大提交。

### 最终交付判定

1. 一位新维护者只读根 README 和 `docs/runbooks/DEPLOYMENT.md`，就能找到**唯一**可用的新栈开发、发布和回滚入口，不需要猜哪份 `cloud-return/staging/split` 是现行版本。
2. 添加一个标准 OpenAI Chat Completions 供应商无需修改已有 PinCC 修补代码；发布前有真实工具调用的验收证据。
3. 系统的授权、Quota、Run、Job、Result Receipt 和 Pi Resume 行为保持正确，不因目录整理和性能优化回退。
4. CI 检查的是实际的新后端、新前端与新构建镜像，而不是已经退出生产的旧项目。
5. Web/API 单独升级不会为了“方便”顺带停止 Supabase/PostgreSQL 或 LiteLLM。
6. 目录架构以稳定职责为核心，旧栈与迁移历史明确归档；没有新增第二套并行 Model Policy、Agent Scheduler 或 Provider Gateway。

---

## 附录 A：直接定位的当前代码路径

- `deploy_to_cloud.sh`：应用发布时调用全栈 `stack.sh down`，Turnstile 测试 key fallback，旧 `/api/v1/health` 探针。
- `deploy/agent/stack.sh`：三个 Compose 项目、基础设施 provisioning、生产 app 启动与 down。
- `deploy/agent/host-nginx-agent-aliyun.conf`：`limit_req_dry_run on`。
- `.github/workflows/ci.yml`：以旧 `backend/`、`frontend/`、根 Dockerfile 为主。
- `new_backend/app/services/model_catalog.py`：`supports_images`、`reasoning_levels`，LiteLLM `/model/info`。
- `new_backend/app/domain/admin/model_policy.py` 与 `app/contracts/admin.py`：现有 ModelDraft/用户授权/修订发布。
- `new_backend/app/adapters/live/pi_rpc.py`：固定 `api="openai-completions"`、每轮 Pi 模型配置。
- `new_backend/app/api/internal.py`：`/internal/model/v1/chat/completions`、HTTP 客户端、quota、上下游错误。
- `new_backend/app/services/session_titles.py`：标题生成的独立模型调用。
- `infra/litellm/provider_compat.py`、`test_provider_compat.py`：PinCC/LiteLLM 限域补丁。
- `new_backend/app/services/agent.py`：调度、Session Lock、事件持久化、Pi 自动恢复。
- `new_backend/app/domain/persistent_conversation/{runs,recovery,usage,af3}.py`：Run、租约、余额、GPU 结算。
- `new_backend/app/db/postgres.py`：SQLite SQL → PostgreSQL 兼容层。
- `new_backend/pi/extension.js`：Agent 可见工具与 `submit_compute`/`pskit_resume`。
- `new_frontend/src/api/http.ts`、`features/chat/{useRunEvents,events}.ts`：HTTP/SSE 解析与重试。
- `new_frontend/src/features/chat/{Composer,ModelPicker,ThinkingSlider}.tsx`：模型/推理/上传行为。
- `new_frontend/src/features/mono/MonoWorkspace.tsx`：页面装配与 Run、消息状态。
- `new_frontend/src/styles/{base,mono,mono-pages,monochrome}.css`：主题样式与覆盖关系。

## 附录 B：当前已知的验证边界

本文依据上述固定 GitHub 分支的代码与仓库内发布记录形成；不是生产环境在线审计，也没有直接执行新版本代码、PostgreSQL 并发基准或真实新供应商兼容测试。Codex 应优先复核最新 HEAD。仓库发布记录显示当前 PinCC 定向补丁已完成针对性真实模型 Tool Calling 验证；**这不能替代下一种新供应商的单独验收**。
