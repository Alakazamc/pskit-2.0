# PSKit 2.0 正式发布缺陷修复与验收报告

报告日期：2026-08-25

交付版本：`0.3.0`

交付实现提交：`fb74611`

线上地址：<https://pskit.bioailab.net>

## 1. 交付结论

PSKit 2.0 `0.3.0` 已部署到 A6000 生产服务器。公网健康与就绪接口均返回 200；Web、科学 Worker 和 Qdrant 容器健康，生产数据库已迁移到 Alembic `0003`，RAG 使用 54 个 1024 维向量片段。

本轮使用生产 API、生产 SQLite、生产队列、真实本地模型和真实 MCP 服务完成了注册登录、流式对话、RAG、7U5E 结构下载、结合位点预测、PAIR、Coral 和 PepCCD 回归。AlphaFold3 保留已有成功任务及 18 个产物，本轮依赖探针也通过；发布验收时 4 张 A6000 均被其他 AF3 容器占用约 46.5/49.1 GB，因此没有杀掉他人任务或强行制造 OOM 来重复推理。

生产数据、旧镜像和旧 Docker volume 均保留；最新上线后备份已经完成 SQLite、产物和 Qdrant 校验，可回滚。

## 2. 本轮缺陷与修复

| 编号 | 缺陷 | 修复 | 验证 | 状态 |
|---|---|---|---|---|
| 1 | 本地仓库落后于服务器，生产代码散落在 overlay | 合并 Agent、科研运行、MCP、本地模型和 AF3 完整生产代码，删除一次性补丁和重复 overlay | 统一镜像从主仓库构建并运行 | 已修复 |
| 2 | 无可靠主线和回滚基线 | 建立 Git 主线；保留发布前基线 `128cd21` | 工作区干净，发布提交可追踪 | 已修复 |
| 3 | 认证限流只在单进程内存中生效，且直接信任转发头 | 改为数据库固定窗口计数；只接受 `TRUSTED_PROXY_IPS` 中直接代理提供的真实 IP | 代理伪造、独立客户端和共享计数测试通过 | 已修复 |
| 4 | 并发请求可绕过任务配额 | SQLite 串行化“检查+入队”，PostgreSQL 使用 advisory lock；增加用户级和全局配额 | 5 并发请求、上限 2 时仅 2 个入队 | 已修复 |
| 5 | AF3 客户端可请求任意 GPU | 增加服务端 GPU allowlist；当前仅允许设备 0 | 配置校验与就绪探针通过 | 已修复 |
| 6 | Agent 断线重试可能重复执行工具 | 持久化 `client_turn_id`、请求哈希、轮次租约和恢复事件 | 相同 turn 重放只订阅原轮次 | 已修复 |
| 7 | 页面只显示“运行中”，看不到结果文件或重试 | 任务页轮询状态，展示文件、大小、下载入口和受控重试 | 生产任务产物可列出和下载 | 已修复 |
| 8 | 管理员缺少用户和运行指标界面 | 增加用户列表、启禁用、角色、会话撤销、指标与审计事件 | API、前端构建和权限测试通过 | 已修复 |
| 9 | RAG 重建会先删除线上集合 | 改为版本化临时集合、数量校验和 Qdrant alias 原子切换，保留旧构建用于回退 | canary/生产均构建 54 points | 已修复 |
| 10 | 数据库模型与生产表结构漂移 | 增加 `0003` 完整迁移；识别已有扩展生产库后校验并安全 stamp | canary 与生产均为 `0003`、`quick_check=ok` | 已修复 |
| 11 | 数据目录写死 Docker 内部 volume 路径 | 改为显式宿主机 bind path `/data1/enine/pskit-production/data` | 数据库和产物在 `/data1` 持久化 | 已修复 |
| 12 | 健康路径错误、Web/Worker 启动依赖可能成环 | 统一 `/api/ready`；Worker 不反向等待 Web healthy | 冷启动后两容器自动 healthy | 已修复 |
| 13 | Coral/PepCCD 配置分散且 PepCCD URL 被 overlay 写死 | URL、工具名统一进入权限 600 的服务器环境文件，Compose 不再写死私有地址 | 两个 MCP 工具发现和真实任务均成功 | 已修复 |
| 14 | 备份不覆盖 MCP 配置，也没有失败目录清理 | 备份 SQLite、产物、Qdrant、Compose 和额外环境文件；加 SHA-256 与保留策略 | 最新备份 4 项校验通过，配置权限 600 | 已修复 |
| 15 | `/tmp` 改为 tmpfs 后 `docker cp` 无法取出备份 | 改用 `docker exec` 二进制流导出；失败自动删除不完整目录 | 备份脚本成功并通过四类文件 SHA-256 校验 | 已修复 |
| 16 | 容器缺少资源和日志边界 | 增加 CPU、内存、PID、tmpfs、停止宽限期和 json-file 轮转 | Compose 模型及线上容器验证通过 | 已修复 |
| 17 | 依赖、类型和镜像缺少发布门禁 | 固定锁文件，加入 Ruff、Mypy、pip-audit、npm audit、测试、Compose 和镜像构建门禁 | 本地全部门禁通过，无已知依赖漏洞 | 已修复 |
| 18 | Windows 打包的 Bash 脚本出现 `sh\r` | 增加 `.gitattributes`，强制 shell/systemd/Docker 资产使用 LF | A6000 smoke 脚本可直接执行 | 已修复 |
| 19 | 容器内部 10706 与旧端口保护规则冲突 | 镜像和 Compose 显式声明容器内兼容端口，宿主机仍发布 10716 | 镜像 import 与 canary 启动通过 | 已修复 |
| 20 | 无持续健康和备份调度；A6000 未启用 user linger，SSH 退出后 systemd user timer 会停止 | 安装用户 crontab，增加 `flock` 防重入、日志限长和幂等安装脚本 | 健康检查每 5 分钟；每日 03:20 校验备份；不依赖 SSH 会话 | 已修复 |

## 3. 生产全链路证据

### 3.1 本次 0.3.0 真实回归

| 链路 | 任务/轮次 ID | 结果 | 产物 |
|---|---|---|---:|
| 普通对话 + Qdrant RAG | `04c90794-d1f2-4ce5-b143-90681a728554` | `succeeded`，返回知识来源 | - |
| 7U5E 下载 → RNA 结合位点 | `ea47032c-24bd-5c1f-8fd4-afebb3632024` | `succeeded` | 6 |
| PAIR 交互预测 | `703bdcd6-b001-445f-afa5-2eb578a530b4` | `succeeded` | 3 |
| Coral MCP | `cf582be3-bd20-50d3-8067-fefd5c81d2bd` | `succeeded`，1 条 RNA 候选 | 3 |
| PepCCD MCP | `237bc254-8fa4-5ee7-9a1e-e5aad8fc7665` | `succeeded`，2 条肽候选 | 3 |

本轮新增 15 个已登记科学产物。专用测试账号已禁用，5 个认证会话已撤销；任务和产物保留用于发布审计。

### 3.2 AlphaFold3 证据边界

- 已有生产成功任务：`5b9f41d3-f9cb-48d3-8a4a-edac723adcb7`。
- 完整耗时：969.231 秒；产物 18 个，包含 `model.cif`、置信度和 evidence manifest。
- 当前探针：Docker daemon、`alphafold3:3.0.1` 镜像和 GPU runtime 均为 `ok`。
- 本轮未重复提交原因：发布时 4 张 A6000 均只有约 2.1 GB 空闲显存，低于 40 GB 安全阈值。此项属于共享 GPU 资源占用，不是代码或配置故障。

## 4. 自动化质量门禁

- 后端：14 项 pytest 全部通过；Ruff 通过；Mypy 16 个稳定边界模块通过。
- 前端：Vue/TypeScript 生产构建通过；ESLint 零警告；Vitest 2 项通过。
- 依赖：`pip-audit` 与 `npm audit --omit=dev` 均未发现已知漏洞。
- 部署：基础 Compose、A6000 science/MCP 组合配置均通过；两个镜像 import、版本和非 root 用户检查通过。
- Canary：认证、SPA、静态资源、迁移、RAG、Web/Worker 健康全部通过后才切换生产。

## 5. 当前生产与回滚

- Web：`pskit2:0.3.0`，镜像 ID `91ba07c368c3...`。
- Worker：`pskit2-science:0.3.0`，镜像 ID `d363d3c7bb12...`。
- Qdrant：`qdrant/qdrant:v1.18.3`。
- 生产运行目录：`/home/enine/pskit-runtime`。
- 生产数据：`/data1/enine/pskit-production/data`。
- 最新验证备份：`/data1/enine/pskit-backups/20260825T033319Z`。
- 旧镜像、旧 named volume 和 Git 基线均保留；未执行 Docker prune。

## 6. 尚需运营管理的风险

1. A6000 根分区约使用 90%，仍有约 178 GB 可用；数据和备份已迁至 `/data1`，但应继续告警并在回滚观察期后定向清理旧镜像。
2. 当前是单 Web + SQLite 架构。数据库限流和任务配额已保证本部署一致性；扩为多机时应迁移 PostgreSQL，并引入独立队列/缓存。
3. Coral、PepCCD、LLM、Embedding、SerpAPI 和校园网属于外部依赖；持续健康检查可以发现异常，但不能消除上游故障。
4. 共享 GPU 当前存在资源竞争。AF3 已设置 40 GB 空闲显存门槛，宁可受控失败，也不与其他科研任务争抢显存。
5. 本轮完成工程发布验收，不替代正式渗透测试、大规模压力测试、灾备恢复演练和模型科学准确性评估。

## 7. 验收方式

1. 打开 <https://pskit.bioailab.net>，注册并登录。
2. 先发送“你好”验证普通对话，再发送“下载 7U5E 并预测 RNA 结合位点”。
3. 在任务页观察 queued/running/succeeded，完成后下载产物。
4. 管理员在用户页检查账号、会话和指标。
5. 运维侧确认 `/api/ready` 四项均为 `ok`，检查用户 crontab 中两个 PSKit 任务，并核对 `logs/health.log` 最近一次成功时间。
