# 新版 PSKit 统一启动与 PostgreSQL 持久化设计

## 目标与边界

用户希望在阿里云用一个命令启动 LiteLLM、Supabase、PostgreSQL 与新版 Python 后端，并让新版后端使用 Supabase 所在的 PostgreSQL 取代生产 SQLite。React 前端继续构建成 `dist`，由阿里云宿主机 Nginx 提供；A6000 继续运行唯一的 AF3 接收器和计算容器，经 WireGuard 主动从阿里云领任务。旧 `pskit.bioailab.net` 不参与切换。

这里的“一起启动”指一个运维入口管理整套服务及健康依赖，而不是限定四个物理容器。自托管 Supabase 已由 Auth、网关、Storage、PostgreSQL 等多个容器组成。**目标仅运行一个 PostgreSQL 容器**：Supabase PostgreSQL 17 的 `postgres` 数据库内保存后端私有 `pskit` schema，同一实例另建 `litellm` 逻辑数据库供 LiteLLM 独占。LiteLLM 的 PostgreSQL 16 旧卷保留为回退备份，但切换后不运行第二个 PostgreSQL 容器。用户明确选择不迁移旧 LiteLLM 数据，将在新 Admin UI 重新添加提供商 API key 和模型；PSKit 自身 SQLite 中的项目、会话、文件、Pi 与配额数据仍须完整迁移。新 LiteLLM 数据库中重新建立 10 美元/30 天团队预算、2 美元/30 天单用户预算及后端虚拟 key。

本设计取代 [阿里云后端迁回设计](2026-10-03-agent-aliyun-backend-return-design.md) 中继续使用 Agent SQLite 的部分；其云端 Python 后端、宿主机 Nginx、A6000 主动领 AF3 任务和单写者切换原则继续适用。旧实施计划已经准备的云端镜像、Nginx 脚本和独立卷只是预备材料，不能直接作为本方案的 PostgreSQL 后端启动。生产流量仍指向 A6000，直到本设计的迁移与验收全部完成。

## 方案选择

1. **采用：统一运维入口，保留现有 Compose 项目，共用一个 PostgreSQL 实例。** `deploy/agent/stack.sh up` 依次启动并检查已有 Supabase 项目、只包含 gateway 的 LiteLLM 项目、后端/AF3 回调代理项目。LiteLLM 连到 Supabase PostgreSQL 内独立的 `litellm` 数据库，Python 业务事务用另一受限账号连接 `postgres` 数据库的 `pskit` schema。保留 Supabase 现有卷、项目名和网络；旧 LiteLLM PostgreSQL 卷停止使用但不删除。
2. **单一 Compose 项目。** 旧 Supabase 与 LiteLLM Compose 都叫 `db`，直接 `include` 会产生命名冲突；即使新 LiteLLM 文件去掉 `db`，接管既有项目容器、卷和网络仍增加一次基础设施迁移。等数据稳定后可单独评估，不作为本次完成条件。
3. **业务数据经 Supabase REST/Data API。** 每次 AF3 租约领取、配额预留、幂等请求和游客升级都需要原子事务及并发控制。把这些操作拆成多次 HTTP 调用难以保持现有语义，本次不用 Data API 承担内部业务持久化。

## 运行拓扑与配置

```text
浏览器 ─ HTTPS ─> 阿里云 Nginx ─┬─ React dist
                                  └─ /api/v1/* → 127.0.0.1:18088 → Python/Pi
                                                                  ├─ Supabase Auth API
                                                                  ├─ Supabase PG17 / postgres.pskit
                                                                  ├─ LiteLLM / claude-opus-4-8
                                                                  └─ Pi 会话持久卷
Aliyun Supabase: Auth + gateway + Storage + 唯一 PostgreSQL 17
                                      └─ litellm 逻辑数据库 → Aliyun LiteLLM gateway
A6000 AF3 receiver ─ WireGuard ─> 10.9.8.1:18184 → 受限回调代理 → Python
```

启动入口从仓库根目录定位配置，不依赖调用者当前工作目录。`up` 先验证 Docker Compose 渲染、受限环境文件、已有卷名和数据库连接信息；按 Supabase → 创建/验证 `litellm` 数据库及角色 → LiteLLM → PostgreSQL `pskit` schema migration → backend/AF3 代理启动，并等待各自健康检查。LiteLLM 的生产 Compose 文件只声明 gateway，不再声明 `db` 服务。`status` 显示三个 Compose 项目的服务健康，并验证运行中只有一个 PostgreSQL 容器；`logs` 需指明所属项目；`down` 按 backend → LiteLLM → Supabase 停止，绝不带 `-v`。启动失败时停止本次新启动的后端，不删除任何已有数据库卷。原有服务可独立运维，但运行手册只给一个统一入口作为常规启动方式。

Supabase PostgreSQL 不发布公网 5432 端口。后端通过 Docker 内网直连 `db:5432/postgres`，用受限的 `pskit_app` 角色访问私有 `pskit` schema；一次性迁移命令使用单独的 DDL 角色。LiteLLM 使用受限的 `litellm` 角色连接 `db:5432/litellm`，它的 Prisma 结构和模型配置只存在该逻辑数据库，不接触 Supabase Auth/Storage 或 `pskit` schema；这个额外数据库由运维工具和 LiteLLM UI 管理，不依赖 Supabase Studio。`pskit` 不加入 PostgREST 的 exposed schemas，浏览器不持有数据库密码。Python 继续验证 Supabase Auth 的身份与 Cookie，业务表只通过现有 Python API 按 `user_id` 授权。连接串放入权限为 `0600` 的环境文件；LiteLLM master/salt/provider key、Supabase key 和 AF3 回调 key 都不写入 Git、镜像或日志。模型仍通过 LiteLLM 虚拟 key、别名和预算控制。

切换 LiteLLM 前，保持旧 `10.9.8.1:4000` 网关及 PostgreSQL 16 运行；使用目标 PostgreSQL 17 的空 `litellm` 数据库启动临时网关，绑定私网 `10.9.8.1:4001`。用户在临时 Admin UI 重配模型/API key，系统重新建立预算与**新的**后端虚拟 key，并分别实测模型调用、工具调用和流式响应。现有 `infra/litellm/.pskit-virtual-key` 是旧数据库的 key，不可因文件已存在就跳过新 key 创建；新 key 存独立的 `0600` 文件。只在新版后端切流量时让生产网关接管 4000，停止旧 PostgreSQL 16 容器并保留原卷。切换前后不让仍在运行的 A6000 后端拿旧 key 调用新数据库。

## PostgreSQL 后端边界

生产模式新增必填 PostgreSQL DSN，缺失、连接失败或 schema 版本不匹配时 readiness 失败，绝不回退 SQLite。Mock 模式保留纯内存替身，便于不启动外部服务的组件检查。旧 SQLite 文件只由离线导入/回退工具读取或写入，不再是新版生产后端的运行数据库。

持久化层采用 `psycopg` 连接池和明确的事务作用域。现有对话、工作区、文件目录、Skill 授权、Run/Event、AF3 任务/产物/审批、Token/GPU 账本、游客身份与清理、OAuth flow、MCP/PDF 并发租约、工具调用等 SQLite store 都切到 PostgreSQL；应用服务与 `/api/v1/` 响应契约不变。`create_app` 不再把同一个 SQLite 文件路径分发给各 store，而是在启动时注入数据库接口。每个请求或后台操作从连接池取得连接，跨表的领取、预留和完成仍在一个事务中。

SQL 迁移必须逐一处理 `?` 参数、`PRAGMA`、`sqlite_master`、`INSERT OR REPLACE/IGNORE`、`rowid` 和 `BEGIN IMMEDIATE`。原来依赖 `rowid` 的表增加显式单调序号并在导入时保留旧排序；Token ledger 的自增 ID 和相关 sequence 导入后校准。首版保留既有 UTC ISO 时间字符串的原值、整数标志与 JSON 字符串，BLOB 映射为 `bytea`，以保证数据和 API 往返一致；日期类型及大文件转 Supabase Storage 属后续独立变更。

并发语义以数据库约束和行锁为准：AF3 claim/recovery 只允许一个 worker 得到有效 lease；请求幂等键与模型调用 guard 由唯一约束保护；Token/GPU 预留、结算和退款在同一事务内更新账本；游客清理锁定账号后拒绝新的项目、文件和 Run 写入；MCP/PDF 租约保持全局并发上限。任务领取可使用 `FOR UPDATE SKIP LOCKED`，用户额度用对应账号/期间行锁或等价的原子更新。不能通过应用进程内锁替代跨进程数据库约束。

数据库结构用版本化 PostgreSQL SQL 迁移管理。迁移由一次性服务执行，后端启动只检查目标版本，不在每个 Web 进程里自动改表。`pskit_app` 不拥有 Supabase `auth`、`storage` 或 LiteLLM 的表；管理员在需要时通过单独迁移角色执行升级。

Pi transcript 仍保存在云端持久卷的 `pi-sessions/` 中，`pi_sessions.session_file` 保存映射后的容器路径。这个卷不是 SQLite 数据库；导入时复制文件、校验哈希与路径，进程重启后可继续已有会话。AF3 产物的字节内容首版存 PostgreSQL `bytea`，保留当前下载鉴权和大小上限。

## 现有数据迁移与切流量

当前新版 Agent 最新业务数据在 A6000 Agent SQLite 卷，Supabase 账号/Auth/Storage 数据已经位于阿里云；云端旧 Agent SQLite 卷是过期副本，不作迁移源。迁移器使用一致性 SQLite 快照，逐表读出所有业务行及旧 `rowid`，导入阿里云 `postgres` 数据库的临时 `pskit_stage` schema；导入过程不会修改 Supabase 的 `auth` 或新建的 `litellm` 数据库。包括实际存在的 33 张业务/迁移表，并处理可能因功能开关尚未创建的表。对每张表核对行数、关键字段、BLOB 长度与 SHA-256，另核对项目、会话、消息、Run、未结算配额、AF3 job/lease、文件、用户 ID 和 Pi transcript 清单；通过后才将临时 schema 激活为 `pskit`。中断的导入保持正式 schema 不变，可以清理临时 schema 后重试。LiteLLM 旧数据库的数据不导入目标 PostgreSQL；新数据库只有新建的模型配置、预算、虚拟 key 和切换后的用量记录。

正式快照前重新检查 A6000 Run/AF3 job、接收器 journal/spool、Pi 进程和云端容量。有未完成工作先等待或对账，不能在执行中的 AF3 任务上强制切换。停 A6000 receiver 领任务，再停唯一新版 A6000 后端写入，制作一致性快照和 Pi 文件清单，传输到阿里云后逐文件校验。只有新的 PostgreSQL 后端完成私网验收，才把 A6000 receiver 指向阿里云私网回调入口；公网 Nginx 最后由阿里云 root 会话执行已审阅的可回退脚本，转向 `127.0.0.1:18088`。绝不同时运行两个生产后端写两份 Agent 数据。

私网验收包括既有账号登录、项目/会话历史、Pi 续聊、上传与下载、Token/GPU 余额、LiteLLM 实际调用、AF3 回调鉴权与受控真实 AF3 的领取/进度/产物/ACK/自动唤醒。公网切换后复核 HTTPS、SSE、上传、OAuth、`/internal/` 拒绝和旧站。所有凭据保持在服务器受限文件，不写进验收记录。

## 故障、回退与完成标准

在 PostgreSQL 后端产生任何新写入前，可以停新后端并恢复 A6000 原后端及 receiver 本机回调，保留源 SQLite 卷。LiteLLM 新配置失败时，旧网关及 PostgreSQL 16 可独立恢复，旧 key 只用于旧数据库。若 PostgreSQL 已产生新会话、账本或任务状态，不能直接启用旧 SQLite；先冻结新后端和 AF3 receiver、对账未 ACK 结果，再用经过往返测试的离线 PostgreSQL→SQLite 导出器生成**新的** SQLite 卷，连同最新 Pi transcript 校验后，才可恢复旧镜像、receiver URL 与公网 Nginx。单个 PostgreSQL 17 实例承载 Supabase、PSKit 与 LiteLLM，备份须包含 `postgres`、`litellm` 两个逻辑数据库及自建角色；读写健康、连接数和磁盘空间都作为切换门槛。源卷、旧云端卷和各快照保留到观察期结束；运维脚本从不删除卷。

开发采用既定 TDD：先用固定版本 PostgreSQL 17 容器写 schema/仓储集成测试，再迁移实现；重点覆盖并发 AF3 claim、重复回调、Token/GPU 预留与退款、游客升级/清理、SQLite 导入及逆向导出、Pi 文件路径、LiteLLM PG17/空库初始化与新 key、Compose 渲染与单实例启动健康。Mock 单测继续可在无 Docker 下运行。每阶段做相应测试、类型/静态检查及镜像构建；生产验收必须用实际私网响应、数据库状态和真实 AF3 记录，不用 mock 结果替代。

完成后，阿里云一个入口命令可启动并检查 Supabase、LiteLLM 和新版 Python/Pi，**只有 Supabase PostgreSQL 17 一个运行中的 PostgreSQL 容器**；生产后端不再打开 Agent SQLite，业务数据在 `postgres.pskit`，LiteLLM 运行在独立逻辑数据库 `litellm`，用户重新添加的模型密钥及重新创建的 10/2 美元预算和新虚拟 key 可用；A6000 只负责 AF3，现有项目、会话、文件、配额和 Pi 历史完整，`agent.bioailab.net` 指向阿里云后端，旧站仍可用。
