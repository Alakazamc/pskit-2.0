# 新版 PSKit 单 PostgreSQL 统一启动 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在阿里云用一个入口启动 Supabase、LiteLLM 与新版 Python 后端，让两个应用共用 Supabase PostgreSQL 17 实例，并将现有 Agent SQLite 历史完整迁入 PostgreSQL。

**Architecture:** Supabase 保留现有 `postgres` 数据库，Python 只访问私有 `pskit` schema；LiteLLM 在同一 PostgreSQL 容器中使用独立的 `litellm` 逻辑数据库。LiteLLM 模型/API key 不迁移，由用户在新 Admin UI 重配；Agent SQLite 和 Pi transcript 必须迁移。保留三个已有 Compose 项目，一个 `stack.sh` 管理启动、健康和停止，A6000 继续主动领 AF3 任务。

**Tech Stack:** Python 3.12、FastAPI、psycopg 3/psycopg_pool、PostgreSQL 17、LiteLLM v1.100.3、Supabase self-hosted、Docker Compose、Pi RPC、pytest、WireGuard、Nginx。

**Spec:** `docs/superpowers/specs/2026-10-03-agent-supabase-postgres-stack-design.md`

## Global Constraints

- 目标是**一个运行中的 PostgreSQL 容器** `supabase/postgres:17.6.1.136`；`postgres.pskit` 与独立逻辑数据库 `litellm` 用不同账号，旧 LiteLLM PostgreSQL 16 卷保留但停止。
- LiteLLM gateway 固定 `ghcr.io/berriai/litellm:v1.100.3`；切换前旧 `10.9.8.1:4000` 继续服务 A6000，候选 gateway 用私网 `10.9.8.1:4001`，用户在候选 UI 重新添加模型/API key。
- 重建团队 **10 美元/30 天**、单用户 **2 美元/30 天**预算和**新的**后端虚拟 key；旧 `.pskit-virtual-key` 不可作为新库有效 key。PSKit Token/GPU 账本与余额不重置。
- 生产后端必须有 PostgreSQL DSN，连接或 schema 版本失败时 readiness 为 503，不允许 SQLite 回退；Mock 单测仍可用内存替身。
- 最新 Agent 数据源是 A6000 卷；云端旧 Agent SQLite 卷不是迁移源。现有项目、聊天、文件、Pi transcript、AF3 任务及配额必须保留。
- 阿里云后端只发布 `127.0.0.1:18088`，React `dist` 由宿主机 Nginx 提供；A6000 AF3 receiver 只主动访问 `10.9.8.1:18184` 的受限入口。旧 `pskit.bioailab.net` 不改。
- Supabase PostgreSQL 5432 不发布公网，`pskit` 不暴露给 PostgREST；连接串、master/salt/provider key、Supabase key 和回调 key 仅放权限 0600 的服务器文件。
- 现有工作区有其他未提交改动；每个提交只暂存本计划指定文件，不清理、覆盖或顺手提交无关文件。旧迁回计划的预备文件可复用，但不能以 SQLite 后端镜像直接切生产。
- 每项代码改动按既定 TDD 写失败用例、观察失败、实现、验证、单独提交。部署前重新核对任务、journal、卷和备份；绝不同时运行两个生产写入后端。

## File Map

- `new_backend/app/db/postgres.py`：连接池、事务上下文与 schema 版本读取，供所有生产 store 使用。
- `new_backend/app/db/postgres_migrations/`：`pskit` 的版本化 SQL，含显式排序序号、约束、索引和迁移入口。
- `new_backend/app/domain/persistent_conversation/{store,workspace,runs,recovery,usage,af3}.py`：保留现有仓储 API，改用 PostgreSQL 事务与并发控制。
- `new_backend/app/domain/{identity_policy,catalog,guest_rate_limit,internal_auth,mcp_capacity,mcp_tool_calls,tool_runs}.py`、`new_backend/app/api/auth.py`、`new_backend/app/services/guest_cleanup.py`：迁移其余 SQLite 业务状态。
- `new_backend/app/{config,main}.py`、`new_backend/app/api/health.py`、`new_backend/pyproject.toml`：生产 DSN 注入、readiness、依赖与 Mock 边界。
- `new_backend/scripts/agent_data_migrate.py`：一致性 SQLite 快照导入 `pskit_stage`、核验并激活，以及 PostgreSQL 到新 SQLite 卷的离线回退导出。
- `infra/litellm/compose.shared-postgres.yaml`、`infra/litellm/bootstrap_pskit.py`：无自带 `db` 的 gateway、候选实例端口、新预算和新虚拟 key。
- `deploy/agent/{compose.postgres.yaml,stack.sh}` 与 `deploy/agent/scripts/provision_shared_postgres.py`：一个入口启动三项目、创建受限数据库账号、只保留一个 PG 容器。
- `deploy/agent/SINGLE_POSTGRES_CUTOVER.md`：备份、私网验收、公网切换和按写入阶段回退；只记录不含秘密的证据。
- `deploy/agent/tests/compose.pg17.test.yaml`、`new_backend/tests/postgres/`、既有 `new_backend/tests/test_*.py`、`deploy/agent/tests/test_single_postgres_stack.py`：固定版测试数据库、PostgreSQL 集成、迁移往返、Compose 与启动契约测试。

## Review Focus

1. **旧 LiteLLM key 文件存在而新数据库没有该 key：** Task 8 测试须断言新 key 存独立 0600 文件，且预算/新 key 确实在候选库建立。
2. **PG16 和 PG17 同时在最终启动命令里运行：** Task 9 渲染与进程测试须断言 shared Compose 无 `db` 服务，`status` 只报告 Supabase 的一个 PG 容器。
3. **并发请求重复领 AF3 任务或重复扣额度：** Tasks 4、5 用两条独立 PostgreSQL 连接同时 claim/reserve，断言唯一 lease 与单次账本结算。
4. **SQLite `rowid` 与自增账本 ID 丢失：** Task 7 在同时间戳、多行样本中验证导入排序与 sequence，反向导出后继续插入仍无冲突。
5. **切换后有新写入却直接恢复旧 SQLite：** Task 10 的运行手册测试须要求先冻结、对账、反向导出到新卷，再启旧镜像及回退 Nginx。

---

### Task 1: PostgreSQL 测试基座与私有 schema

**Files:** Create `new_backend/app/db/postgres.py`, `new_backend/app/db/postgres_migrations/{__init__.py,001_core.sql,002_components.sql}`, `deploy/agent/tests/compose.pg17.test.yaml`, `new_backend/tests/postgres/conftest.py`, `new_backend/tests/postgres/test_schema.py`; modify `new_backend/pyproject.toml`。

**Interfaces:** `PostgresDatabase(dsn: str, *, schema: str = "pskit", max_size: int = 10)` 提供 `connection()`、`transaction()` 上下文及 `check_schema_version(required: int) -> None`；`migrate_postgres(dsn: str, *, schema: str = "pskit") -> None` 由独立迁移命令调用，Web 进程只检查版本。连接池供 Tasks 2–6 注入。

- [ ] **Red:** 写 `test_private_schema_and_version`：固定 PG17 测试库运行 `migrate_postgres` 后检查核心与组件表、`agent_jobs(run_id,tool_call_id)` 唯一约束、`agent_token_entries.id` identity、显式 `ordinal`、迁移版本；重复运行后行数和版本不变。
- [ ] **Run red:** 用 `docker compose -f deploy/agent/tests/compose.pg17.test.yaml up -d --wait` 启动仅绑定 `127.0.0.1:15433` 的 `supabase/postgres:17.6.1.136`，设置测试专用 `TEST_POSTGRES_DSN`，运行 `pytest new_backend/tests/postgres/test_schema.py -q`；预期因迁移接口尚不存在而失败。
- [ ] **Green:** 增加 `psycopg[binary]>=3.2,<4`、`psycopg_pool>=3.2,<4`，创建从现有 SQLite schema 对照生成的全部业务表、索引、约束及迁移表。`schema` 参数通过安全标识符引用；迁移仅由单独命令执行。测试夹具每例用独立 schema，结束清理。
- [ ] **Verify:** 同一 PG17 测试通过，`ruff check new_backend/app/db new_backend/tests/postgres` 通过，确认 Mock 模式不需要 PostgreSQL。
- [ ] **Commit:** 只提交本任务文件，`feat(db): add private Postgres schema and pool`。

### Task 2: 项目、会话、消息、Run 与恢复状态

**Files:** Modify `new_backend/app/domain/persistent_conversation/{store,workspace,runs,recovery}.py`；add `new_backend/tests/postgres/test_conversation.py`；update existing `test_workspace_crud.py`, `test_message_parts.py`, `test_run_leases.py`, `test_queued_run_recovery.py` to use the PG fixture for Pi-mode persistence cases。

**Interfaces:** `PersistentConversationStore(database: PostgresDatabase, *, af3_min_gpu_memory_mb: int = 0)` 保留原有公开方法和返回类型；每个多语句操作在 `database.transaction()` 中完成。后续 Tasks 3–4 共用这个 store。

- [ ] **Red:** 写 `test_workspace_and_run_survive_reopen`：项目→会话→分段消息→Run/Event→关闭/重开连接池后内容及顺序相同；写 `test_two_resumers_claim_one_run`，并行连接只有一个获得有效 Run lease，另一个没有写入事件。
- [ ] **Run red:** `pytest new_backend/tests/postgres/test_conversation.py -q`；预期旧构造器只接受 SQLite path 或竞争 claim 不正确。
- [ ] **Green:** 将这四个模块改为显式连接/事务，`rowid` 查询改显式 `ordinal`；保留 user_id 检查、SSE cursor、事件序号和 Pi session_file 映射。旧 SQLite 运行实现仅供 Task 7 离线迁移工具，生产 store 不再打开文件。
- [ ] **Verify:** PG 新测试与所列既有行为测试通过；再运行 `ruff check` 涉及文件。
- [ ] **Commit:** 只提交本任务文件，`feat(db): persist conversations and runs in Postgres`。

### Task 3: Token/GPU 配额与模型调用幂等

**Files:** Modify `new_backend/app/domain/persistent_conversation/usage.py`；add `new_backend/tests/postgres/test_quota.py`；update `test_atomic_quota.py`, `test_token_period.py`, `test_af3_gpu_reconciliation.py`, `test_usage_entries.py` to use the PG fixture for persistent cases。

**Interfaces:** 保持现有 `PersistentConversationStore` 配额方法签名；预留、结算、释放、管理员调整和 model call guard 使用 Task 1 的事务与唯一约束。

- [ ] **Red:** 写 `test_two_reservations_cannot_exceed_token_limit`：同用户同期间余额只够一次调用，两连接并发只有一笔有效预留；写 `test_duplicate_settlement_is_once` 与 GPU 跨日结算/取消断言账本和余额只变一次。
- [ ] **Run red:** `pytest new_backend/tests/postgres/test_quota.py -q`；预期竞争或 SQLite SQL 失败。
- [ ] **Green:** 原 `BEGIN IMMEDIATE` 改针对用户/期间的行锁或原子条件更新，`INSERT OR ...` 改 PostgreSQL `ON CONFLICT`，保留现有配额期间与用户默认值。账本写入与状态更新保持单事务。
- [ ] **Verify:** 新 PG 与所列既有配额测试通过，检查无模型调用可绕开预留 guard。
- [ ] **Commit:** 只提交本任务文件，`feat(db): make quotas atomic on Postgres`。

### Task 4: AF3 租约、审批与产物

**Files:** Modify `new_backend/app/domain/persistent_conversation/af3.py`；add `new_backend/tests/postgres/test_af3.py`；update `test_af3_compute_claim.py`, `test_af3_public_idempotency.py`, `test_af3_approvals.py`, `test_artifact_files.py`, `test_af3_execution_timeout.py` to use the PG fixture for persistent cases。

**Interfaces:** 保持既有 job/approval/artifact API；数据库中 BLOB 用 `bytea`，`ordinal` 保留旧 job 顺序。A6000 接收器继续调用原 HTTP 契约，`lease_token` 与 worker ID 不变。

- [ ] **Red:** 写 `test_parallel_claim_returns_one_lease`、`test_replayed_callback_does_not_duplicate_artifact_or_gpu_charge`、`test_artifact_round_trip_preserves_sha256`；使用两个 PostgreSQL 连接及同一 job/tool_call_id。
- [ ] **Run red:** `pytest new_backend/tests/postgres/test_af3.py -q`；预期旧 SQLite 代码失败。
- [ ] **Green:** claim 使用行锁/`SKIP LOCKED` 与条件更新；批准、租约续期、进度、产物注册、ACK 和过期恢复共享事务及唯一约束。保持单产物 20 MiB 上限和 ownership 检查。
- [ ] **Verify:** 新 PG 与所列既有 AF3 测试通过，A6000 receiver/proxy 契约测试通过。
- [ ] **Commit:** 只提交本任务文件，`feat(db): move AF3 leases and artifacts to Postgres`。

### Task 5: 身份、游客清理、文件目录与 Skill 授权

**Files:** Modify `new_backend/app/domain/{identity_policy,catalog,guest_rate_limit}.py`, `new_backend/app/services/guest_cleanup.py`; add `new_backend/tests/postgres/test_identity_catalog.py`; update `test_identity_policy.py`, `test_guest_cleanup.py`, `test_guest_email_upgrade.py`, `test_catalog_files.py`, `test_skill_authorization.py` to use the PG fixture for persistent cases。

**Interfaces:** IdentityPolicyStore、CatalogStore、GuestRateLimiter 构造器接收 `PostgresDatabase`，现有业务方法与 FastAPI 响应保持不变；游客清理事务锁住 `account_tiers`，清理同一 `user_id` 的所有 Agent 表。

- [ ] **Red:** 写 `test_guest_cleanup_blocks_concurrent_upload_and_run`、`test_upgrade_keeps_same_user_and_data`、`test_file_bytes_and_skill_grants_survive_reopen`；同一账号通过不同连接并发操作。
- [ ] **Run red:** `pytest new_backend/tests/postgres/test_identity_catalog.py -q`；预期旧 SQLite store 无法接受 PG pool。
- [ ] **Green:** 用 PostgreSQL 元数据代替 `sqlite_master`，去除 WAL/PRAGMA；文件 `raw_content` 用 `bytea`，保持上传大小、owner、技能授权、匿名速率限制与清理审计语义。
- [ ] **Verify:** 新 PG 与所列既有测试通过，确认 `auth.users` 未被直接改写。
- [ ] **Commit:** 只提交本任务文件，`feat(db): move identity and catalog to Postgres`。

### Task 6: OAuth、MCP/PDF、工具记录与应用接线

**Files:** Modify `new_backend/app/api/auth.py`, `new_backend/app/domain/{internal_auth,mcp_capacity,mcp_tool_calls,tool_runs}.py`, `new_backend/app/{config,main}.py`, `new_backend/app/api/health.py`, `new_backend/.env.example`, `new_backend/README.md`, `deploy/agent/backend.Dockerfile`; add `new_backend/tests/postgres/test_app_wiring.py`; update `test_component_migrations.py`, `test_mcp_capacity.py`, `test_pdf_shared_capacity.py`, `test_tool_runs.py`, `test_health.py`, `test_live_adapters.py` and every Pi-mode test still setting `agent_db_path` (discover with `rg -l 'agent_db_path|agent.sqlite3|PersistentConversationStore\(' new_backend/tests`) to use the shared PG fixture. Keep SQLite-only migration/maintenance tests for Task 7。

**Interfaces:** `Settings.database_url: str` 读 `RESEARCH_AGENT_DATABASE_URL`；`create_app(settings)` 在 `mode="live"` 时要求 DSN、注入同一个 `PostgresDatabase`，lifespan 建连/关池，`GET /health/ready` 检查连接及 Task 1 schema 版本。Mock 保持现有内存替身，旧 SQLite 路径只交离线脚本。

- [ ] **Red:** 写 `test_live_rejects_missing_or_stale_database`，缺 DSN/错版本返回配置失败或 readiness 503；写 `test_tools_and_oauth_survive_reopen`，并发 MCP/PDF 只允许配置的上限、内部 HMAC key 不重复生成。
- [ ] **Run red:** `pytest new_backend/tests/postgres/test_app_wiring.py -q`；预期缺 DSN 校验或旧 SQLite 连接错误。
- [ ] **Green:** 迁移 OAuth flow、工具调用、MCP/PDF 全局租约和内部鉴权数据；移除生产启动的 SQLite 连接和镜像默认 `RESEARCH_AGENT_DB_PATH`，更新文档/依赖。进程崩溃的租约恢复仍由持久表处理。
- [ ] **Verify:** 新 PG 测试及 `pytest new_backend/tests --ignore=new_backend/tests/test_schema_version.py -q` 通过；在无 DB 的纯 Mock 单测仍可运行；`ruff check new_backend` 通过。旧 SQLite schema 版本测试在 Task 7 改为仅测试离线迁移工具。
- [ ] **Commit:** 只提交本任务文件，`feat(db): wire live backend to Supabase Postgres`。

### Task 7: A6000 SQLite 快照导入与安全反向导出

**Files:** Create `new_backend/scripts/agent_data_migrate.py`, `new_backend/tests/postgres/test_agent_data_migrate.py`; modify `new_backend/tests/test_schema_version.py` to exercise the offline SQLite migration path, and `deploy/agent/scripts/agent_data_snapshot.py` only if import needs a documented manifest field。

**Interfaces:** `import_sqlite_snapshot(sqlite_path: Path, database_url: str, *, staging_schema: str = "pskit_stage") -> ImportReport` 校验后激活 `pskit`；`export_sqlite_snapshot(database_url: str, destination: Path, *, schema: str = "pskit") -> ExportReport` 只写新文件。CLI 不打印用户内容或凭据。

- [ ] **Red:** 写 `test_import_preserves_all_tables_bytes_and_order`：含同时间戳消息、AF3 BLOB、配额预留、Pi 路径与可选表的 SQLite fixture 导入后行数/哈希/`ordinal` 一致；写 `test_export_round_trip_and_next_insert`，导出后旧镜像 schema 可读且新 ID 不冲突；写导入中途失败时正式 `pskit` 不变。
- [ ] **Run red:** `pytest new_backend/tests/postgres/test_agent_data_migrate.py -q`；预期迁移脚本缺失。
- [ ] **Green:** 复用现有一致性快照工具，按版本化映射导入所有实际表，校准 identity/ordinal sequence，验证后原子激活 staging schema；反向导出创建全新 SQLite 文件并检查完整性。Pi transcript 另按清单复制、SHA-256 校验并将路径映射到 `/data/pi-sessions/`。
- [ ] **Verify:** PG 往返测试、`test_schema_version.py`、`test_agent_data_snapshot.py` 及完整 `pytest new_backend/tests -q` 通过；人工审阅一份脱敏计数报告，确认 `auth`/`litellm` 未被导入器触碰。
- [ ] **Commit:** 只提交本任务文件，`feat(migrate): round-trip Agent data between SQLite and Postgres`。

### Task 8: LiteLLM 使用 Supabase PostgreSQL 的候选实例

**Files:** Create `infra/litellm/compose.shared-postgres.yaml`, `deploy/agent/scripts/provision_shared_postgres.py`, `deploy/agent/tests/test_shared_litellm_postgres.py`; modify `infra/litellm/{bootstrap_pskit.py,README.md,.env.example,.gitignore}`；对尚未跟踪的 `infra/litellm/{compose.yaml,config.yaml}` 先审阅并在本任务单独纳入版本控制，不包含 `.env` 或虚拟 key。

**Interfaces:** `provision_shared_postgres(admin_dsn: str, *, litellm_password: str, pskit_password: str) -> None` 幂等创建 `litellm` DB/role 与 `pskit_app` role；shared Compose 只含 `gateway`，加入现有 Supabase Docker 网络连接 `db:5432/litellm`。同一文件用 `-p pskit-agent-litellm-candidate` 发布候选 `10.9.8.1:4001`，最终用 `-p pskit-agent-litellm` 接管原 gateway 并发布 4000；候选与原 gateway 在切换前并行。`bootstrap_pskit.py` 接受 `--base-url`、`--key-file`，默认保持旧端口/路径但候选调用必须传独立目标。

- [ ] **Red:** 写 `test_provision_creates_separate_database_and_roles`、`test_app_role_cannot_read_auth`、`test_shared_compose_has_no_db_service`、`test_bootstrap_never_reuses_old_key_file`；假 HTTP 验证预算分别是 10/2 美元、候选 key 文件 0600，日志不含密钥。
- [ ] **Run red:** `pytest deploy/agent/tests/test_shared_litellm_postgres.py -q`；预期目标文件/CLI 不存在。
- [ ] **Green:** 增加最小化 DB/角色 provision、只含 gateway 的候选 Compose、可指定端口与 key 路径的 bootstrap。旧 PG16 与 4000 保持运行；候选依赖真实 Supabase PG17，Prisma 自动建表只限 `litellm` DB。
- [ ] **Verify:** 测试、Compose `config --quiet` 通过；在本地 PG17 空库启动固定 LiteLLM 镜像，验证 readiness、模型 UI、预算、新虚拟 key 及使用本地假上游的工具调用。真实提供商 key 由用户在阿里云候选 UI 填写，不进入测试或 Git。
- [ ] **Commit:** 只提交本任务所列已审阅文件，`feat(litellm): use shared Supabase Postgres instance`。

### Task 9: 统一启动入口与阿里云 Compose 覆盖

**Files:** Create `deploy/agent/{compose.postgres.yaml,stack.sh}`, `deploy/agent/tests/test_single_postgres_stack.py`; modify `deploy/agent/{cloud.backend.env.example,README.md}`；复用 `compose.yaml`、`compose.cloud.yaml` 与 Nginx/AF3 私网脚本。

**Interfaces:** `deploy/agent/stack.sh up|status|logs <project>|down` 使用已存在的 Supabase、LiteLLM、Agent 项目及卷，不创建第二个 PG；backend 通过 0600 env 注入 `RESEARCH_AGENT_DATABASE_URL`，Pi 仍挂 `/data/pi-sessions`，前端不启动 `web` 容器。

- [ ] **Red:** 写 `test_up_orders_supabase_litellm_migration_backend` 用假 docker 命令记录调用；写 `test_final_status_has_one_postgres`、`test_render_has_no_public_db_port_and_up_excludes_web`、`test_down_never_uses_volume_flag`，同时检查缺密钥/健康失败时不会启动 backend。
- [ ] **Run red:** `pytest deploy/agent/tests/test_single_postgres_stack.py -q`；预期缺统一入口或覆盖文件。
- [ ] **Green:** 实现相对路径安全的一个入口；Supabase `up --wait`、创建/检查 DB、LiteLLM `up --wait`、一次性 schema migration、Agent backend/proxy `up --wait`。`status` 明确显示唯一 `supabase-db` 与无旧 PG16 运行；脚本不打印渲染后的凭据，也不使用 `down -v`。
- [ ] **Verify:** 假 docker 测试、实际 Compose 配置渲染、`bash -n deploy/agent/stack.sh`、已有部署测试及 backend 镜像构建通过。
- [ ] **Commit:** 只提交本任务文件，`feat(deploy): start Agent stack with one Postgres`。

### Task 10: 迁移手册、私网验收与生产切换

**Files:** Create `deploy/agent/SINGLE_POSTGRES_CUTOVER.md`, `deploy/agent/tests/test_single_postgres_cutover.py`; use existing `deploy/agent/host-nginx-agent-aliyun.conf`、root 安装脚本、AF3 receiver 覆盖文件；记录只写手册的部署证据节。

**Interfaces:** 手册先备份 `postgres`、新 `litellm`、自建角色及两个旧数据卷；受控切换顺序为 LiteLLM 候选 4001 完成配置和验证 → A6000 冻结写入 → Agent SQLite/Pi 导入并校验 → 停旧 4000 gateway/PG16、用同一新库 gateway 接管 4000 → 运行统一入口启动云端后端并私网验收 → 接收器改指向 `10.9.8.1:18184` → 用户在阿里云 root 会话运行 Nginx 脚本。旧 PG16 卷不删；任何新写入后的反向导出与对账是恢复旧后端的前提。

- [ ] **Red:** 写 `test_runbook_blocks_cutover_with_active_job_or_unacked_journal`、`test_runbook_requires_reverse_export_after_new_write`、`test_runbook_keeps_old_litellm_until_new_key_works`，断言脚本/手册列出实际 gate 与回退命令。
- [ ] **Run red:** `pytest deploy/agent/tests/test_single_postgres_cutover.py -q`；预期文件缺失。
- [ ] **Green:** 写准确的双主机命令、密钥权限、磁盘/连接数/备份验证、旧站及 `/internal/` 检查。先在本地 Docker 进行完整替身切换；然后在阿里云准备固定镜像与空库候选，等用户填好模型 key 后做私网实际调用。正式停写和公网切流量只在所有检查通过且用户已可运行 root 脚本时执行。
- [ ] **Verify:** 新手册测试、`pytest deploy/agent/tests -q`、后端 PG 集成测试、真实候选 LiteLLM readiness/模型调用、AF3 回调鉴权与一次受控真实 AF3 任务均有匿名化证据；HTTPS/SSE/登录/上传与旧站健康检查通过。失败按对应阶段回退，不删任何卷。
- [ ] **Commit:** 只提交手册、测试与不含凭据的证据记录，`docs(deploy): document single-Postgres cutover and rollback`。

## Plan Self-Review

- 规格的单 PG 拓扑/账号和权限由 Tasks 1、8、9 覆盖；全部 Agent store 由 Tasks 2–6 覆盖；SQLite/Pi 保留及双向回退由 Task 7 覆盖；旧 LiteLLM 不迁移与新 key/预算由 Task 8 覆盖；私网、公网切换与备份由 Task 10 覆盖。
- 五项 Review Focus 分别有 Tasks 8、9、3/4、7、10 的独立失败用例。每项有 Red→Green→Verify→Commit，执行者可逐项审阅。
- 当前工作区的其他未提交文件不会被计划清理或顺手纳入。已有 `infra/litellm` 目录目前未跟踪，Task 8 必须先确认其中没有密钥再只提交显式列出的源文件。
- 用户此前已选择**当前工作区、由我逐项执行**；本计划审阅通过后沿用该执行方式，不再重复询问。阿里云宿主机 Nginx root 操作仍须用户在准备完成后运行可回退脚本。
