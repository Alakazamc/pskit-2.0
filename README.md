# PSKit 2.0

科研 Agent 工作台：React 前端、FastAPI API、Pi JSON RPC、PostgreSQL 17、LiteLLM 模型网关，以及 OpenSandbox 工作区。GPU 计算通过独立接收器和已审核的 MCP 服务执行。

## 当前开发入口

开始前阅读 [本地开发与云端背景](docs/DEV_CLOUD_CONTEXT.md)，再检查 `git status --short --branch`。日常开发同时运行本地前后端；mock 配置与真实联调数据隔离。具体命令、端口、Windows SSH 方式只维护在背景文档中。

| 位置 | 职责 |
| --- | --- |
| `new_frontend/` | React/Vite、会话与文件富 UI、管理页面；见 [前端约定](new_frontend/AGENTS.md) |
| `new_backend/app/` | FastAPI API、权限、账本、模型策略、Agent 及工作区领域模块 |
| `new_backend/pi/` | 固定 Pi 扩展与 JSON RPC 工具边界 |
| `contracts/` | OpenAPI、事件与计算契约、Release Manifest schema |
| `infra/` | Supabase/PostgreSQL、LiteLLM、OpenSandbox 配置与独立生命周期 |
| `deploy/agent/` | 应用 Compose、接收器与受控发布工具 |
| `scripts/ci_new_stack.py` | 新栈隔离验证；见 [CI 说明](docs/runbooks/NEW_STACK_CI.md) |
| `docs/architecture/` | [现状审计](docs/architecture/CURRENT_STATE_AUDIT.md)、[整改计划](docs/architecture/REMEDIATION_PLAN.md)及逐阶段证据 |

用户文件与产物按所有权访问。浏览器草稿留在用户设备；服务端会话/Run/Job/用量使用 PostgreSQL，SQLite 仅保留兼容测试及历史迁移用途。模型工具只能在真实执行成功后声明文件已生成。工作区不是宿主根目录；隔离、安全授权和资源额度仍由既有 OpenSandbox/gVisor 边界控制。

## 检查与构建

本地命令与合成测试的环境要求见 [新栈 CI 手册](docs/runbooks/NEW_STACK_CI.md)。新工作流在本分支 push 和所有 PR 运行，不自动发布云端。公开 API 与生成类型通过独立契约检查保持一致；PostgreSQL 测试必须在专用 PG17 上执行，不能静默跳过。

## 发布与回退

唯一日常应用发布入口是 [pskitctl 与受控发布手册](docs/runbooks/RELEASE_CONTROL.md)，读取已经验收的不可变 Manifest。API 与 Web 可分别更新，基础设施、数据库迁移、执行节点升级使用独立流程。缺少活动状态、准入保护或关键 smoke 时拒绝发布。

- [当前部署拓扑与首次安装](deploy/agent/DEPLOYMENT.md)
- [独立 Staging](deploy/agent/STAGING.md)
- [发布操作与历史职责](docs/runbooks/DEPLOYMENT.md)

本轮整改没有连接云端、发布生产、修改真实数据库或删除历史数据。受控工具的本地故障测试不等于实际 Staging 演练；发布准入适配器、真实模型资格和 RPO/RTO 必须另行验收。

## 历史栈与路径

Vue/LangGraph/Qdrant 旧栈仍位于 `frontend/`、`backend/`，保留用于历史维护和恢复，不是新版默认入口。原 README 逐字节保存于 [历史索引](docs/archive/README.md)。由于 Compose source bind、旧 CI 与回退版本仍引用现有路径，本轮保留 `new_backend/`、`new_frontend/`；未来纯路径迁移的前置清单见 [目录迁移计划](docs/architecture/phases/phase-6/CHANGE_SUMMARY.md)。
