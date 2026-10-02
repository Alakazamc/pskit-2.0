# 新版 PSKit A6000 后端迁移 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 保留阿里云前端、Supabase 与 HTTPS，让新版 Python/Pi 后端和 AF3 在 A6000 运行，并保留现有账号与 Agent 会话。

**Architecture:** 阿里云 Nginx 将 `/api/v1/` 经 WireGuard 转给 A6000 的私网 API 代理，静态页面及限定的 OAuth 路径仍由阿里云 Web 提供。A6000 后端经阿里云私网代理访问原 Supabase，现有 host 网络 AF3 接收器改用本机回环回调代理；停止云端后端并迁移其 SQLite/Pi 数据后才切流量。

**Tech Stack:** React 静态镜像、FastAPI/Pi、Docker Compose、Nginx、WireGuard、Supabase、SQLite、Python pytest。

**Spec:** `docs/superpowers/specs/2026-10-02-agent-a6000-backend-migration-design.md`

**后续修订：** 用户决定前端由阿里云宿主机 Nginx 直接服务 `dist`，不保留常驻 Web 容器。Task 1 的独立静态 Web 成果只作为备用配置；Task 2 的公网配置改为静态 `root`、SPA `try_files` 和 OAuth 到本机 Envoy；Task 6 从当前固定版前端镜像提取 `dist`，root 安装到 `/var/www/agent.bioailab.net`，公网验收通过后停云端 Web 容器。具体命令和证据记录在 `deploy/agent/A6000_MIGRATION.md`。

## Global Constraints

- 阿里云 WireGuard `10.9.8.1`，A6000 `10.9.8.2`；`agent.bioailab.net` 的 TLS 与 Supabase 留阿里云；旧 `pskit.bioailab.net` 及 `10.9.8.2:10716` 不修改。
- 云端 Web 只绑定 `127.0.0.1:18085`；Supabase 仍绑定 `127.0.0.1:18130`，由源地址仅允许 `10.9.8.2` 的 Nginx 代理另听 `10.9.8.1:18130`。
- A6000 后端只发布 `127.0.0.1:18089`；host 网络 API 代理只听 `10.9.8.2:18088` 且只允许 `10.9.8.1`；AF3 回调代理只发布 `127.0.0.1:18185`。
- 仅一个新版生产后端/AF3 接收器可处理真实数据。先冻结写入、确认无未完成 AF3 claim、备份并迁移 SQLite/WAL/Pi 会话，再接管；不使用 `docker compose down -v`。
- 所有凭据只存权限 `0600` 的部署文件，不写入镜像、仓库、前端或日志。保留现有 GPU 默认每日 0 分钟；真实 AF3 验证只能给指定测试账号授予临时额度，未经授权不得改为全员额度。
- 继续使用现有固定版本基础镜像和 Supabase；模型替身、无 SMTP 与 Google OAuth 凭据的状态必须如实标记，迁移不算这些外部服务的正式接入。
- 工作区已有其他未提交改动；每次只暂存本计划对应文件，不覆盖或带入其他改动。

## Review Focus

1. 云端 Web 重启时没有 `backend` Docker DNS：Task 1 的容器测试必须仍返回 SPA 200、OAuth 路径可用、`/api/v1/` 不落到旧后端。
2. 公网错误转发 `/internal/` 或 Supabase 管理路径：Task 2 的路由测试和 Task 6 的公网探针必须返回 404。
3. 从非指定 WireGuard 地址访问 API/Supabase：Tasks 2–3 的配置测试和 Task 6 的实机探针必须拒绝，而指定两端可达。
4. SQLite WAL、Pi 会话或权限在迁移时丢失：Task 4 用带 WAL 的临时数据库和 Pi 文件测试快照/恢复，Task 6 核对旧会话。
5. 切换时出现两个接收器或重复 AF3 完成：Task 5 的配置测试与 Task 6 的队列/journal/ACK 检查必须证明只有一个消费者且回调幂等。

---

### Task 1: 云端 Web 可独立于后端启动

**Files:** Create `deploy/agent/web.static.conf`, `deploy/agent/compose.frontend-cloud.yaml`, `deploy/agent/tests/test_split_web_routes.py`; modify `deploy/agent/web.Dockerfile`。

**Interfaces:** `web.Dockerfile` 新增构建参数 `WEB_NGINX_CONFIG`，默认仍为 `deploy/agent/web.conf`；使用 `deploy/agent/web.static.conf` 构建独立静态镜像。`compose.frontend-cloud.yaml` 的项目名仍为 `pskit-agent-cloud`，只定义 `web`，连接 `pskit-agent-supabase_default`，发布 `127.0.0.1:18085:80`，不依赖 `backend`。

- [ ] **Red:** 加 `test_static_web_starts_without_backend_dns`：仅提供 `api-gw` 替身，无 `backend` 容器时，`/session/x` 为 200、`/auth/v1/authorize` 可代理、`/api/v1/ping` 与 `/internal/x` 为 404。运行 `pytest deploy/agent/tests/test_split_web_routes.py -q`，确认因静态配置/镜像不存在而失败。
- [ ] **Green:** 加静态 Nginx 配置和 Dockerfile 构建参数；用 `docker build -f deploy/agent/web.Dockerfile --build-arg WEB_NGINX_CONFIG=deploy/agent/web.static.conf -t pskit-agent-web:split-test .` 构建，运行相同测试及 `AGENT_WEB_IMAGE=pskit-agent-web:split-test docker compose -f deploy/agent/compose.frontend-cloud.yaml config --quiet`，预期全部通过。
- [ ] **Review/commit:** 确认默认 `web.conf` 行为未改变；只提交本任务文件，提交信息 `feat(deploy): decouple cloud web from backend`。

### Task 2: 阿里云公网 API 路由与私网 Supabase 入口

**Files:** Create `deploy/agent/host-nginx-agent-split.conf`, `deploy/agent/host-nginx-supabase-private.conf`, `deploy/agent/tests/test_split_cloud_ingress.py`。

**Interfaces:** 公网站点沿用证书与 `127.0.0.1:18085` 页面路由；新增优先于 `/` 的 `/api/v1/` → `http://10.9.8.2:18088`，保留 Host/真实 IP/HTTPS 协议，关闭 SSE 响应缓冲和上传请求缓冲。切换时备份并**替换** `/etc/nginx/conf.d/agent.bioailab.net.conf`，不能并存两个同名虚拟主机。私网配置安装到独立的 `/etc/nginx/conf.d/agent-supabase-private.conf`，只听 `10.9.8.1:18130`、只允许 `10.9.8.2`、转给 `127.0.0.1:18130`；旧站文件不在输出路径内。

- [ ] **Red:** 加 `test_split_public_routes_and_private_denials`，断言 `/internal/` 与 Supabase 管理路径未获公网代理、API 目标和超时/缓冲设置准确；加 `test_supabase_relay_is_wireguard_only`，断言监听、allow/deny 和回环上游。运行 `pytest deploy/agent/tests/test_split_cloud_ingress.py -q`，确认缺文件失败。
- [ ] **Green:** 写两份独立 Nginx 配置，运行同一测试，预期通过；`nginx -t` 留给 Task 6 在有真实证书的云主机执行。
- [ ] **Review/commit:** 核对旧 `host-nginx-agent.conf` 和 `pskit.bioailab.net` 不受影响；只提交本任务文件，提交信息 `feat(deploy): route split agent API over WireGuard`。

### Task 3: A6000 新版后端与受限 API 代理

**Files:** Create `deploy/agent/compose.a6000.yaml`, `deploy/agent/a6000.env.example`, `deploy/agent/a6000.backend.env.example`, `deploy/agent/api-a6000.conf`, `deploy/agent/tests/test_a6000_compose.py`。

**Interfaces:** 独立项目 `pskit-agent-a6000` 提供 `backend`、`api-proxy`、`af3-callback-proxy` 和只在 `private-test` profile 启用的模型替身。`a6000.env.example` 定义固定镜像标签、worker ID 与受限环境文件路径；`a6000.backend.env.example` 定义服务端变量。后端环境 `SUPABASE_URL=http://10.9.8.1:18130`、`SUPABASE_PUBLIC_URL=https://agent.bioailab.net`、公开前端/API URL 均为同一 HTTPS 域名；`api-proxy` 使用 host 网络和 `nginx:1.28.0-alpine@sha256:30f1c0d78e0ad60901648be663a710bdadf19e4c10ac6782c235200619158284`，只听 `10.9.8.2:18088`，代理到 `127.0.0.1:18089`，拒绝 `/internal/`。现有 `new_backend/scripts/af3_callback_proxy.py` 继续校验回调密钥和 worker ID；A6000 后端使用与现有 `receiver.cloud.env` 一致的回调密钥，不在日志中输出。

- [ ] **Red:** 加 `test_a6000_stack_has_one_loopback_backend_and_no_web` 和 `test_api_proxy_allows_only_cloud_and_public_api`：渲染 Compose 断言无 Supabase 本地容器、无公网 Docker 端口，配置断言 `.1` allow/其他 deny、`/internal/` 404。运行 `pytest deploy/agent/tests/test_a6000_compose.py -q`，确认失败。
- [ ] **Green:** 写 Compose、环境示例和代理配置；运行同一测试与 `docker compose --env-file deploy/agent/a6000.env.example -f deploy/agent/compose.a6000.yaml --profile private-test config --quiet`，预期通过。此任务只准备文件，不在 A6000 启动第二个生产后端。
- [ ] **Review/commit:** 核对 `backend` 有可出站访问云端 Supabase 的网络且镜像标签不为 `latest`；只提交本任务文件，提交信息 `feat(deploy): define A6000 backend stack`。

### Task 4: Agent SQLite 与 Pi 会话的一致性迁移

**Files:** Create `deploy/agent/scripts/agent_data_snapshot.py`, `deploy/agent/tests/test_agent_data_snapshot.py`；modify `deploy/agent/OPERATIONS.md`。

**Interfaces:** `snapshot(source: Path, destination: Path) -> dict[str, str]` 在**旧后端停止后**把 `agent.sqlite3` 连同已提交 WAL 内容制成完整 SQLite 目标库、复制 `pi-sessions/`，输出文件 SHA-256 manifest；`restore(snapshot_dir: Path, target: Path) -> None` 校验 manifest、拒绝覆盖非空目标。执行脚本时将 Docker 命名卷与备份目录分别只读/读写挂入临时 Python 容器，目标卷所有权恢复为后端 UID/GID `10001:10001`。跨主机传输只用校验过的归档，不输出会话或密钥内容。

- [ ] **Red:** 加 `test_snapshot_includes_committed_wal_and_pi_files`、`test_restore_rejects_checksum_mismatch_and_nonempty_target`：用临时 WAL 模式数据库、Pi 文件与篡改归档断言数据完整、拒绝覆盖。运行 `pytest deploy/agent/tests/test_agent_data_snapshot.py -q`，确认缺接口失败。
- [ ] **Green:** 实现两个接口和 CLI（`snapshot`/`restore` 子命令）；运行同一测试，预期通过；在 `OPERATIONS.md` 写明云端卷 `pskit-agent-cloud_agent_data`、停止顺序、归档校验和 A6000 恢复命令。
- [ ] **Review/commit:** 核对不会复制 Supabase 数据库或 Storage 卷，也不会在后端仍写入时执行；只提交本任务文件，提交信息 `feat(deploy): snapshot durable agent data`。

### Task 5: 接收器改走 A6000 本机回调

**Files:** Create `deploy/agent/compose.a6000-receiver.yaml`, `deploy/agent/tests/test_a6000_receiver_config.py`；modify `deploy/agent/OPERATIONS.md`。

**Interfaces:** 仅在显式 `cutover` profile 启动新 receiver；镜像固定为现有 A6000 已验证的 `af3_mar5_jhli_2026_0923:v1`，保留 `--worker-id a6000-af3-cloud-1`、host 网络、`/data/jhli/pskit-af3-receiver-test-20261002/spool` 持久挂载和现有计算容器，`--api-url http://127.0.0.1:18185`。停止旧 receiver 并确认退出后才启动新 receiver；不自动创建第二个 GPU compute 容器。

- [ ] **Red:** 加 `test_receiver_preserves_spool_and_is_not_started_by_default`：断言 profile、host 网络、本机回调、原持久目录与 worker ID；断言 Compose 不含 compute 服务。运行 `pytest deploy/agent/tests/test_a6000_receiver_config.py -q`，确认失败。
- [ ] **Green:** 写 receiver overlay 与 `OPERATIONS.md` 的原容器备份/停机/接管步骤；运行相同测试和 Compose 渲染检查，预期通过。
- [ ] **Review/commit:** 对照 A6000 现有 `docker inspect` 的挂载与镜像标签，明确 journal 有未确认任务时禁止切换；只提交本任务文件，提交信息 `feat(deploy): prepare local AF3 receiver cutover`。

### Task 6: 私网验收、数据切换和公网放量

**Files:** Create `deploy/agent/A6000_MIGRATION.md`, `deploy/agent/tests/smoke_split.py`；modify `deploy/agent/OPERATIONS.md`。

**Interfaces:** `smoke_split.py --base-url https://agent.bioailab.net` 从受限凭据文件读取测试账号，不打印凭据；检查登录、原项目/会话、`/api/v1/` 流式事件、上传、额度、`/internal/` 404，另用只读请求检查旧站 200。真实 AF3 单任务单独验证 claim、进度、`simulation=false`、产物、GPU 结算、ACK、spool/journal 清理和 Pi 自动唤醒。

- [ ] **Red/Green:** 用 HTTP 替身给 `smoke_split.py` 写测试，断言上述请求路径、SSE 逐段到达、任何日志无密码/令牌；先确认缺脚本失败，再实现并运行对应测试通过。
- [ ] **预检:** 在两机核对旧站健康、镜像 digest、私网 ACL 与宿主机防火墙、Supabase 连通性、Agent 卷备份、无运行中 Agent/AF3 claim、接收器 journal 空闲；未授权测试账号 GPU 额度时，记录真实 AF3 未验证，不改全员额度。
- [ ] **切换:** 停阿里云新版后端及回调代理，做 Task 4 一致性迁移，在 A6000 启动唯一生产后端；确认私网登录、历史会话、Pi 与 AF3 回调后，通过阿里云云助手 root 执行 `nginx -t`、安装并 reload 公网 `/api/v1/` 路由，再换独立静态 Web 镜像；成功后停用阿里云旧 `10.9.8.1:18184` 回调入口。不停止旧 `pskit`。
- [ ] **验收/回退:** 运行公网 smoke、拒绝路径、非许可源地址和旧站检查；仅有授权的指定测试账号才运行真实 AF3。失败时冻结 A6000 写入、保留 journal/卷，按迁移后的最新数据反向恢复后才重启云端后端；记录实际结果与未验证项到 `A6000_MIGRATION.md`，只提交文档及本任务脚本。

## Execution Handoff

先按 Tasks 1–5 交付可审阅文件并做本地红绿验证；Task 6 先准备运行手册与探针，再在 A6000/阿里云按停机、备份、恢复、私网验证、公网切换的顺序执行。旧站和 Supabase 不迁移。任何缺少 SMTP、真实模型或指定账号 GPU 授权的项目保持明确未完成状态，不以可打开登录页代替端到端验收。
