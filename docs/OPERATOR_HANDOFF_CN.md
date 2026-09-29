# PSKit 2.0 交接手册

更新日期：2026-09-29。本文是可公开的代码与操作交接版。**真实服务器 IP、个人 SSH 密钥路径、运行目录及卷挂载点不放入公开仓库**，由项目所有者另行保存私密部署路径附录。不要把 GitHub 合并、容器健康或 MCP 工具发现当作真实科研推理成功。

## 1. 交接范围与当前结论

- 源码仓库：<https://github.com/Alakazamc/pskit-2.0>，发布分支为 `main`。本地检出目录由维护者自行指定，以下代码路径均相对仓库根目录。
- 本次维护只改 PSKit 2.0 的 Agent、任务、证据、报告、错误处理、测试与文档；**不改** CORAL、PepCCD、AlphaFold3、INABe、PAIR 等模型实现或权重。
- 仓库自动化覆盖后端、前端、Chromium 和从零启动的 Docker 栈；真实 MCP 推理及 AF3 GPU 任务必须在目标环境单独验收。
- 2026-09-29 已在目标 A6000 **本机**部署合并提交 `462a3ea` 对应的 Web/Worker 镜像，两个容器健康，现用数据库迁移至 `0003`。`/api/health` 中的应用版本字段仍为 `0.3.0`，要以运行容器的镜像 ID 和 OCI revision 核对代码版本。
- 本机浏览器已验收注册、登录、退出、对话记录持久化、工具目录和任务页；普通对话仍因大模型出网失败返回明确的不可用提示。CORAL/PepCCD 当前 MCP 就绪探针失败，`/api/ready` 为 503，不能宣称全链路通过。
- 同日从云端向 A6000 的私网服务发起健康请求超时；依项目所有者选择，本轮只做 A6000 本机部署与测试，**未**修改云端、VPN 或校园网认证。精确端点和路径见私密附录。

## 2. 文件与数据位置

代码文件位置完整列于第 3 节；本节列出部署文件的**相对位置及确认方法**。主机专属绝对路径由私密部署路径附录保存，避免公开内部拓扑。

| 用途 | 位置 | 核查状态与注意事项 |
| --- | --- | --- |
| Git 源码检出 | 运行者指定的 `<SOURCE_CHECKOUT>`；仓库根目录有 `README.md` | 源码目录不一定控制当前正在运行的容器；升级前核对实际 Compose 标签和镜像 ID。 |
| Docker Compose 工作目录 | 运行容器标签 `com.docker.compose.project.working_dir` 指向的 `<RUNTIME_DIR>` | 当前目标机的实际运行目录与源码检出目录不同；路径与有权访问的账号见私密附录。 |
| Compose 配置 | 仓库根目录 `compose.yaml`；科学叠加示例 `compose.science.a6000.example.yaml`、`compose.alphafold3.a6000.example.yaml`；服务器特有叠加文件位于 `<RUNTIME_DIR>` | 从运行容器标签 `com.docker.compose.project.config_files` 读取真实文件列表。不要把示例文件直接当作线上配置。 |
| 私密环境文件 | `<RUNTIME_DIR>/.env.docker`；仓库模板 `.env.docker.example` | 不提交或发送密钥值。以当前运行容器标签和运维人员确认为准。 |
| Web/Worker 的持久化数据 | `compose.yaml` 的默认数据卷，及服务器叠加配置和 `DATABASE_URL` 指定的实际路径 | **不能只看默认卷**：目标机同时保留旧卷和较新的现用库。备份、迁移、回滚前必须从运行容器的 `DATABASE_URL` 和挂载信息确认真正使用的数据；精确路径见私密附录。 |
| Qdrant 数据 | `compose.yaml` 的默认向量卷；目标机通过服务器专属存储叠加层将容器内 `/qdrant/storage` 改为独立数据盘的 bind mount | 迁移前卷保留供回退；实际运行挂载、叠加文件和备份位置见私密附录。 |
| 科学模型与数据库 | 由科学 Worker 专属 Compose 叠加层只读挂载 | 权重/数据库不在 Git 或离线交付包中；精确路径见私密附录。 |
| 云端旧服务 | 与当前 PSKit 2.0 仓库分别识别 | 不凭容器名字或 SPA 页面把旧服务当成本仓库的部署目标；对应端口和挂载见私密附录。 |
| 备份脚本与备份目录 | 仓库 `scripts/backup_runtime.sh`；默认在项目的 `backups/` | 实际路径由 `PSKIT_BACKUP_ROOT` 和运行目录决定。备份包含用户数据/私密配置，必须限制权限。 |

同一宿主机可能同时运行正式实例与演示实例，不能按名称模式批量停止。`scripts/pskit2_ctl.sh status` 报告的是源码目录下的原生进程，不是 Docker 服务状态。

## 3. 仓库代码导航

| 问题/职责 | 文件位置（相对仓库根目录） |
| --- | --- |
| 配置项和环境变量 | `backend/app/config.py`、`.env.example`、`.env.docker.example` |
| Agent 对话与调用决策 | `backend/app/agent/orchestrator.py`、`backend/app/agent/graph.py`、`backend/app/tools/catalog.py` |
| 科学工具适配和 MCP 协议 | `backend/app/tools/mcp_adapters.py`、`backend/app/tools/science_adapters.py`、`backend/app/tasks/worker.py` |
| 任务排队、租约、输入校验 | `backend/app/tasks/service.py`、`backend/app/tasks/validation.py`、`backend/app/tasks/worker.py` |
| 研究运行、候选与证据 | `backend/app/api/research.py`、`backend/app/research/service.py`、`backend/app/db/models.py` |
| 报告和下载 | `backend/app/tools/reports.py`、`backend/app/artifacts/service.py`、`backend/app/api/files.py` |
| 登录、权限与会话 | `backend/app/api/auth.py`、`backend/app/middleware/`、`backend/app/api/agent.py` |
| 用户页面 | `frontend/src/views/AgentView.vue`、`frontend/src/views/TasksView.vue`、`frontend/src/lib/agentConversation.ts` |
| Docker 与科学 Worker 叠加层 | `compose.yaml`、`compose.science.a6000.example.yaml`、`compose.alphafold3.a6000.example.yaml`、`Dockerfile.science` |
| 数据迁移、备份和交付 | `scripts/migrate_db.py`、`scripts/backup_runtime.sh`、`scripts/export_offline_delivery.sh` |
| 自动化验证 | `.github/workflows/ci.yml`、`backend/tests/`、`frontend/e2e/`、`scripts/test_delivery.py` |
| 部署操作说明 | `DOCKER_QUICKSTART.md`、`DEPLOYMENT.md`；以实际运行目录的 Compose 配置为准 |

## 4. 安全升级顺序（由有运行目录权限的运维人员执行）

1. 先记录当前容器镜像 ID、Compose 文件列表、Git 提交和容器健康。确认正在使用的 `<RUNTIME_DIR>`，不要在另一个源码目录执行命令后误认为已经更新线上。
2. 确认维护窗口和正在运行的科研任务；备份数据库、任务产物、Qdrant 数据与私密配置。优先使用 `scripts/backup_runtime.sh`，并实际检查备份目录中的文件和权限。**不要**使用 `docker compose down --volumes`；那会删除持久化卷。
3. 在独立目录检出已合并的 `main`，对照运行目录的私有叠加配置、许可证和模型挂载。不要覆盖服务器专属 `.env.docker` 或未提交的运维文件；先审阅差异，再制作新镜像。
4. 以运行目录实际 Compose 文件列表执行 `docker compose ... config --quiet`，然后按其迁移/升级流程更新 Web 与 Worker。AlphaFold3 的 Docker socket 只应继续挂载到经过审查的科学 Worker，绝不能挂到公开 Web 容器。
5. 升级后检查 `docker compose ps`、`/api/health`、`/api/ready`、Worker 心跳、日志和登录；再按第 5 节完成真实任务验收。异常时使用备份和原镜像回滚，切勿在有新写入后盲目回退数据库文件。

仓库的可复制单机 Docker 安装流程见 `DOCKER_QUICKSTART.md`。它是**新环境**的模板，不能直接替代当前目标机的多文件 Compose 运行配置。

## 5. 验收矩阵：健康 ≠ 推理

| 层次 | 操作与通过标准 | 目前证据 |
| --- | --- | --- |
| 代码 | CI 的 backend、frontend、image 三项通过；本地可在 `backend/` 用 `uv sync --frozen --extra dev` 后执行 `uv run pytest -q tests`，在 `frontend/` 用 `npm ci` 后执行 `npm test -- --run`、`npm run e2e`、`npm run build`。 | 代码/CI 可验证；不能替代目标服务器验收。 |
| 服务 | 目标机本机 `/api/health` 为 JSON；`/api/ready` 和管理员 Doctor 应显示 Worker、MCP、模型路径与 GPU 依赖的真实状态。 | 新 Web/Worker 和 Qdrant 健康，数据库/Worker 心跳正常；CORAL/PepCCD MCP 当前不可连，`/api/ready` 为 503；跨云访问未通过。 |
| 账号与隔离 | 测试账号注册/登录、创建会话、发送消息、查看任务、下载自己产物；第二账号不得读取第一账号任务和文件。 | 本机实际浏览器已通过注册、登录、退出、会话和消息持久化、工具与任务页；下载和跨账号隔离在新部署上尚未重新抽验。 |
| PDB/RAG | 用有效 PDB 条目完成结构获取，确认真实文件能下载；RAG 问答应返回可核对的来源，而非只有回答文本。 | 需目标环境验收外部访问和索引。 |
| PepCCD | 从有效研究运行提交一次小规模候选生成，等待 Worker 到 `succeeded`，核对 `candidates.json/csv`、原始 MCP 响应、候选数量、参数和版本溯源；失败则保留错误类型与任务 ID。 | 现用库中有历史成功任务，但当前 MCP 探针失败；新部署未重新执行推理。 |
| CORAL | 使用远端 MCP 支持的 PDB/chain 参数实际生成 RNA 候选，核对结果文件和候选记录；远端若无法访问 PDB，应修远端网络/代理，而不是误报 PSKit 已跑通。 | 现用库中有历史成功任务，但当前 MCP 探针失败；新部署未重新执行推理。 |
| AlphaFold3 | 在资源可用且确认队列空闲后提交一次经批准的最小真实任务，核对非空、可解析的结构文件与 `af3_evidence_manifest.json`、结果 JSON、下载权限和失败清理。 | Docker/镜像/GPU 环境探针正常且有历史成功任务；新部署未重新执行真实 GPU 任务。 |
| 跨云链路 | 云端能访问 `http://<A6000_VPN_ADDRESS>:<PORT>/api/health`，然后从预定公开入口完成登录和下载。 | 2026-09-29 云端请求超时；需要网络管理员排查 WireGuard 握手、AllowedIPs、主机防火墙及网关转发。精确地址见私密附录。 |

`scripts/smoke_live_backend.sh` 只检查健康和页面；`scripts/smoke_backend.py` 会排队但不真正执行 AF3。不要将它们的成功写成“全部科学模型全链路通过”。真实测试前须确认测试账号、费用/显存预算、样例数据与模型服务负责人。

## 6. 本轮代码改动与未完成事项

- 研究运行的人工证据更新只替换人工记录，不再清除系统生成的工具证据；科研报告显示候选生成器版本、任务/产物 ID、参数哈希和指标，证据省略与截断均有明确提示。
- CORAL/PepCCD 的 MCP 结果处理复用统一提取逻辑；PepCCD 工具名/schema 不匹配保留可操作的错误原因。AF3 资源预检失败不再被后续输出整理异常遮蔽。模型本身未改动。
- 本地回归：后端 63 项、前端 28 项、Chromium 11 项、交付脚本 5 项通过；以本次 PR 的 CI 结果作为合并依据。真实目标服务器的科学任务验收仍未完成。
- 运维待办：为 A6000 提供经批准的稳定出网，恢复大模型及 MCP，确认私网/公开入口，执行第 5 节未验收的真实样例，并按第 8 节处理根盘容量。已完成的本机部署和备份不等于公开上线。

## 7. 故障定位与交接边界

- 对话失败：先看 Web 日志、LLM 连通性、Agent turn 状态，再看 Worker；不要用一条健康响应推断 LLM 可对话。
- 科学任务卡住：按任务 ID 查 `tasks` 状态、租约、Worker 日志与该任务产物目录；成功状态必须有实际结果文件，不能只看 MCP `tools/list`。
- MCP 失败：分清连接/握手、工具名或 schema 不匹配、远端模型执行异常、远端取外部 PDB 失败；不要把远端主机故障归为本机模型损坏。
- AF3 失败：查看 GPU 可用显存、镜像/数据挂载、Worker 任务日志及归属容器。超时清理只应触碰能确认属于该任务的容器。
- 结果复现：保留任务 ID、研究运行 ID、输入参数/哈希、模型返回的版本（未知时写“未知”）、原始响应、结构/表格/证据文件及其哈希；预测结果不得写成实验证实。
- 安全：所有 API key、校园网/服务器密码、SSH 私钥和模型许可证都留在私密配置/密钥管理中，**不得**写进 Git、工单截图或交接文档。此前以聊天等方式共享过的凭证应轮换。交接账号应使用个人独立账户，不共用管理员身份。

正式交付判定：本仓库 CI 全绿、目标部署版本可追溯、备份/回滚经过验证、上述真实科学任务有可下载证据、跨云与公开入口可用、账号隔离合格。任何一项缺失时，应把它标成“待验收”，而不是宣称平台已完全上线。

## 8. 存储容量与迁移边界

2026-09-29 的目标机盘点显示：有空间充足的独立数据盘，但系统根盘接近满载。PSKit 的现用数据库、产物和备份已放在数据盘；本次又在备份后将活跃 Qdrant 存储迁到该盘，核对索引记录后保留旧卷供回退。根盘上更大的占用包括共享的 AlphaFold3 数据库、系统 Docker 镜像/容器写入层和其他用户正在运行的任务。因此，**只搬 PSKit 数据卷不能解决根盘容量问题**，也不能对整台共享 Docker 主机执行全局清理。

项目所有者已决定**暂不继续切换服务器存储位置**。保留现有 Qdrant 数据盘挂载和回退卷，不迁移共享 AlphaFold3 数据库或 Docker `data-root`，不停止其他用户容器。当前根盘只余约 2.9 GB，容量风险尚未解除；须持续监控，安排新的大体量任务或正式发布前先协调容量处置，不得把本次 Qdrant 迁移写成整机扩容完成。

系统 Docker 的 `data-root` 是 daemon 级配置，不是单个 Compose 项目的存储开关。修改它会影响同一 daemon 管理的其他容器；迁移须由服务器管理员协调维护窗口、备份和所有者确认。建议先清点运行容器及所属人，保存应用和 Docker 数据，预拷贝到独立数据盘；停机后完成最终同步，再切换 daemon 配置，逐一核对 PSKit、其他用户容器、GPU、网络、卷和回滚能力。确认稳定前保留原目录，**不得**因为新目录可见就删除旧目录。若改用 containerd image store，还需单独核对 containerd 的数据目录；`data-root` 不会自动迁移它。参见 [Docker 官方 daemon 存储配置](https://docs.docker.com/engine/daemon/)。

共享模型数据库也只能由其所有者和管理员协调迁移；保留原挂载路径、权限和校验是前置条件。没有维护窗口、其他用户确认或可验证回滚时，记录风险并停止迁移，不用 `prune`、强制停止容器或移动公共目录来制造短暂空闲空间。目标机的绝对路径、实际盘量和负责人待确认项保存在私密部署路径附录。
