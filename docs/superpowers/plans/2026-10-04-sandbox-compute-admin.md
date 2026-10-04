# 用户沙箱、通用计算与管理台实施总计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有新版 PSKit 中完成每用户沙箱、同门科研模型统一返回协议与 CPU/GPU 用量上报，以及统一模型开放和额度管理页面。

**Architecture:** 保持 React + Vite、Python、Supabase、LiteLLM 与单 PostgreSQL。先补齐现有 Docker 沙箱 provider，再将 AF3 执行机制推广成通用 Job/账本，管理台通过 Python 的 JWT/RBAC 操作这些业务对象。

**Tech Stack:** Python 3.12、FastAPI、Pydantic v2、psycopg、Docker Engine API、现有 Pi RPC、MCP 1.x、React、TypeScript、Radix、TanStack Query。

**Spec:** [已提交并供用户审阅的统一对接方案](/home/jhli/pskit-2.0/docs/research/2026-10-04-sandbox-compute-integration-proposal.md)。用户在该方案后要求“开始实现”，据此采用它作为书面设计基线。

## Global Constraints

- 容器归属采用每用户独立，同一用户多个会话复用；会话拥有独立 cwd、transcript 与 Pi RPC 进程。
- 沿用当前工作区。2026-10-04 用户授权 A、管理后端与管理前端并行实施：先确定 API 合约，按文件划分职责，主代理整合共享配置、迁移、生成类型及最终验收；不另建 worktree。
- 复用现有 Docker manager；OpenSandbox 保持 provider 替换边界，不同时启动第二套容器调度器。
- 镜像和 Pi 版本固定；保持 `mcp>=1.26,<2`，本轮不以协议升级为前提。
- GPU 服务独立于用户 CPU 沙箱；Pi 只获得 Run 范围权限，不持提供商或管理密钥。
- 平台统一返回协议 `ExecutionReport = Completed | Pending | Failed`，不要求 SDK 统一测量任意远程服务。维护者上报严格非负整数用量；未知为 `null`，已知未消耗才为 `0`。
- `UsageReport` 含 `wall_ms / cpu_core_ms / gpu_device_ms / peak_memory_bytes / peak_gpu_memory_bytes / gpu_count / source`；source 为 service_reported、measured、estimated、unknown。服务声明 required_usage；终态缺少必需值不能验收或结算。
- 包装器只适配函数、HTTP、MCP 的业务输出；cgroup/CUDA/NVML 不作为通用 SDK 前提。内部服务上报是责任人的声明；准入与硬停止能力分别声明，不把自报值声称为外部核验账单。失败也报告并结算已消耗用量。
- CPU 核毫秒、GPU 设备毫秒整数计账；旧 AF3 walltime 明确标为 legacy_wall，不虚构 CUDA 耗时。
- 沿用单 PostgreSQL 与任务 lease/outbox；不引入 Redis/Celery 或重复额度真相源。
- Token 沿用现有月额度，GPU 沿用用户现有每日限制；CPU 默认额度必须显式配置。测试示例额度不写成生产默认值。
- 后端新增 API 是真实持久业务接口；前端 mock 使用同一接口契约，仅用于开发和测试，不增加写死演示数据。
- 前端保持中英双语、主题适配，遵守 [AGENTS.md](/home/jhli/pskit-2.0/new_frontend/AGENTS.md)。
- 本计划先在本地/隔离 Staging 验收；生产发布采用现有部署 skill 和发布记录，不把测试记录迁入生产。
- `.venv/bin/python` 命令从 `new_backend/` 执行，`npm` 命令从 `new_frontend/` 执行；仓库相对路径统一从项目根目录解析。
- 实施基线 PostgreSQL schema 为 v3；本次 B 已新增下一版 004_compute.sql（v4），后续 A/C 按实际实施顺序分配版本，不为计划编号创建空迁移。迁移显式顺序执行，启动不静默改生产 schema；应用回退必须使用能识别新 schema/Job 的兼容镜像，不能直接重启严格只接受 schema v3 的旧镜像。
- 旧 `docs/adr/0001-final-stack.md` 对 Vue/LangGraph/Celery 的选择属于旧系统；本轮依据用户已指定的新版 React/Pi/单 PostgreSQL 架构，不恢复旧技术栈。

## Review Focus

1. 活跃 Pi turn 超过 30 分钟，或 manager 重启：容器不能被误认为空闲而停止。由 A-1/A-2 测试。
2. GPU 任务完成报告 ACK 丢失、重复或过期：不再次执行或重复扣额。由 B-2/B-3/B-5 测试。
3. 并发提交和 UTC 跨日：不能超额准入，昨日未结算预占不能消失。由 B-2 测试。
4. UI 已打开后撤销管理员或模型权限：缓存不能继续放行真实请求。由 C-1/C-2 测试。
5. 取消、超时与未知用量：界面不得声称 GPU 已停止或额度已释放。由 B-3/C-4 测试。

## 独立交付计划与执行顺序

| 阶段 | 实施文件 | 可独立验收的结果 |
| --- | --- | --- |
| A | [用户沙箱](/home/jhli/pskit-2.0/docs/superpowers/plans/2026-10-04-user-sandbox.md) | 多会话安全复用、活动回收、文件/产物往返、排空换镜像 |
| B | [通用计算与 SDK](/home/jhli/pskit-2.0/docs/superpowers/plans/2026-10-04-compute-sdk-metering.md) | 同门服务注册、持久 Job、统一结果/用量协议及预算、ACK 重放和 Pi 唤醒 |
| C | [管理台](/home/jhli/pskit-2.0/docs/superpowers/plans/2026-10-04-agent-admin-console.md) | JWT/RBAC 管理、逐用户模型发布、额度和任务运营、中英页面 |

用户本次明确先修订并实施 B；B 不依赖未实现的 A 迁移或 C 管理页面，A/C 保留后续独立交付。每个任务遵循一个公开行为的 red → green，再补下一行为；不是一次写完全部测试再实现。每个阶段分批 commit，沿用用户“无需再次询问 commit”的授权。

## 已授权的 TDD 测试边界

1. **沙箱边界**：私有 manager/bridge HTTP API、公开 `SandboxPiRunner.prompt` 合约、文件导入导出接口；Docker HTTP 与 Pi RPC 是外部替身边界。
2. **计算边界**：公开 Job/usage API、可信 worker claim/heartbeat/result/receipt 协议、SDK 的函数注册与执行接口、Pi 自动继续可观察结果。
3. **管理边界**：管理员 JWT HTTP API、用户模型目录与真实发送入口、前端管理页实际交互；OpenAPI mock 与真实 API 返回同一类型。
4. **集成边界**：隔离 PostgreSQL schema、独立 Compose/Staging 的合成账户与 CPU 执行任务；GPU 适配器用替身验证协议，真实 A6000 取消/计量验收另记录，不凭替身宣称硬件验证通过。

不增加依赖私有 helper、直接修改内部字典或重复计算被测算法的测试。

## 总体验收与交付

- [x] A、B、C 各任务 red/green 记录和 commit 已完成；A/C 后端 `06c1685`、前端 `37a01ff`，部署与交付记录随收尾批次提交。
- [x] 本地 PostgreSQL 测试实际运行；若 `TEST_POSTGRES_DSN` 未设置导致 skip，不作为通过。
- [x] 后端相关测试、ruff；前端相关测试、typecheck、lint、build；结果逐项记录。
- [x] 本地隔离验收：Compose 完成用户沙箱复用、文件往返与恢复；CPU 任务、ACK 重放、Pi 唤醒及管理权限通过真实 HTTP/PostgreSQL 边界验证，范围分别记录。
- [x] 发布前审查源码、版本化 migration、配额账本兼容、服务端 secret 边界及回退说明。
- [x] 文档明确实际已验证范围、CPU/GPU 硬限制能力、尚待真实模型接口的信息。

2026-10-04 用户已提供统一返回协议并要求“修改计划，然后直接开始实现”；据此直接执行 B，沿用既有 TDD 边界、当前工作区、原生逐项执行和分批 commit 授权，不增加重复审批。


## 2026-10-04 交付进度

- [x] B：服务目录、持久 Job、薄 SDK、HTTP/MCP adapter、用量与配额、可靠回执重放、Pi 自动唤醒。
- [x] B 验证：全后端 503 项通过，无 PostgreSQL skip；ruff、前端 typecheck/lint/build、SDK wheel 构建通过。
- [x] A/C：沙箱 A-1～A-5、管理后端 C-1～C-4 与管理前端 C-5～C-6 已实现并完成本地隔离验收。
- [ ] 真实模型/GPU 联调与生产发布。

具体接入方法见 [COMPUTE_SERVICES.md](/home/jhli/pskit-2.0/new_backend/COMPUTE_SERVICES.md)，证据与范围裁定见 [交付记录](/home/jhli/pskit-2.0/docs/research/2026-10-04-compute-sdk-delivery.md)。旧 AF3 物理调度与通用 UUID 锁尚未合并，切换同一设备前须排空；预算追加接口未开放。

## 2026-10-05 A/C 验证汇总

完整后端603项、前端193项、部署115项、管理HTTP/PG验收56项通过；PostgreSQL无skip。真实Docker项目oct04j验收通过，Chromium完成双语/双主题/双宽度56页检查。Ruff逐改动文件对比无新增问题，旧测试存量问题另记。完整结果、审查修复、原始失败记录与部署边界见 [A/C交付记录](../../research/2026-10-04-sandbox-admin-delivery.md)。本轮未部署到远程Staging或生产，真实GPU验收仍独立进行。
