# 新版 PSKit 后端迁回阿里云 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将新版 Python/Pi 后端及最新 Agent 数据迁回阿里云，同时让 A6000 AF3 接收器继续通过 WireGuard 主动领任务并回传结果。

**Architecture:** 阿里云宿主机 Nginx 继续提供 React `dist`，把 `/api/v1/` 转向本机回环后端；Supabase、LiteLLM、Agent SQLite 和 Pi 会话均在云端。A6000 只保留单个 AF3 receiver、持久 journal/spool 和现有计算容器，向云端 `10.9.8.1:18184` 的受限回调代理轮询。

**Tech Stack:** Docker Compose、Python 3.12/FastAPI/Pi、SQLite、Supabase、LiteLLM、Nginx、WireGuard、pytest。

**Spec:** `docs/superpowers/specs/2026-10-03-agent-aliyun-backend-return-design.md`

## Global Constraints

- 阿里云 WireGuard 地址 `10.9.8.1`；A6000 为 `10.9.8.2`。AF3 只由 A6000 主动访问云端 `10.9.8.1:18184`，不新增 A6000 入站服务。
- 公网域名保持 `agent.bioailab.net`；React `dist` 继续由宿主机 Nginx 服务，`/api/v1/` 只能代理到阿里云 `127.0.0.1:18088`，`/internal/` 必须 404。
- 云端后端和 AF3 回调代理分别只发布 `127.0.0.1:18088`、`127.0.0.1:18185`；后端镜像固定标签，不用 `latest`。
- A6000 接收器保持 worker ID `a6000-af3-cloud-1`、镜像 `af3_mar5_jhli_2026_0923:v1`、现有 spool/journal 和计算容器。
- 最新数据源是 A6000 卷 `pskit-agent-a6000_agent_data`；云端旧卷 `pskit-agent-cloud_agent_data` 不可作为生产数据或被原地覆盖。Supabase/Postgres/Storage 不迁移。
- LiteLLM 第一版模型别名为 `claude-opus-4-8`，沿用已有用户和团队预算；Token/GPU 配额不能因迁移重置。密钥文件权限 `0600`，不打印或提交。
- `ecs-user` 无 sudo；任何宿主机 Nginx 更改须由用户在阿里云 root 会话运行准备好的脚本。旧 `pskit.bioailab.net` 与旧容器不改。
- 当前工作区有其他未提交改动；每个提交只暂存本计划指定文件，不清理或覆盖其余文件。用户之前已选择在当前工作区逐项执行。

## File Map

- `deploy/agent/compose.cloud-return.yaml`：为既有云端 Compose 栈指定**新的** Agent 卷，并让静态 Web 不参与后端服务启动。
- `deploy/agent/compose.a6000-receiver.yaml`：把 receiver 的 `--api-url` 变成可配置值，默认保留当前本机地址；迁回时显式设置云端私网地址。
- `deploy/agent/host-nginx-agent-aliyun.conf`：本机后端版公开 vhost，保留现有 React 静态站和精确 OAuth 路由。
- `deploy/agent/scripts/enable_private_af3_ingress.sh`：受限 AF3 私网入口的可回退 root 操作。
- `deploy/agent/scripts/install_host_nginx_agent_cloud.sh`：公网 API 上游切换的可回退 root 操作。
- `deploy/agent/ALIYUN_RETURN.md`：镜像、密钥、冻结、备份、恢复、验收和回退的逐步运行手册。
- `deploy/agent/tests/test_aliyun_return_compose.py`、`test_aliyun_return_ingress.py`：新增配置及切换脚本的契约检查。
- `deploy/agent/tests/test_a6000_receiver_config.py`：扩展 receiver 本机默认值与云端覆盖值的渲染检查。

## Review Focus

1. 云端旧 `agent_data` 卷已有过期会话：Task 1 测试必须断言生产 Compose 指向新卷，旧卷不被挂入新后端。
2. receiver 未显式设置云端 URL：Task 2 测试必须断言默认仍是本机地址，并断言覆盖值准确为 `http://10.9.8.1:18184`，防止误连旧后端。
3. 公网路由误暴露 `/internal/` 或破坏 OAuth/SSE：Task 3 测试必须断言拒绝内部路径、保留精确 OAuth、关闭响应缓冲与请求缓冲。
4. 切换期间出现新的 AF3 claim 或未 ACK journal：Task 4 运行手册必须要求重新查询并在非空时中止；Task 5 实际执行时记录结果。
5. 云端产生新写入后回退：Task 4 手册必须禁止直接重启 A6000 旧卷，并给出先停止、对账、反向快照再启动的顺序；Task 5 实际演练检查回退条件。

---

### Task 1: 云端后端的独立新数据卷

**Files:**
- Create: `deploy/agent/compose.cloud-return.yaml`
- Create: `deploy/agent/tests/test_aliyun_return_compose.py`

**Interfaces:**
- Consumes: `compose.yaml` + `compose.cloud.yaml` 的现有 `backend`、`af3-callback-proxy`、`agent_data`、Supabase 网络和环境变量。
- Produces: 叠加 `compose.cloud-return.yaml` 后 `backend` 挂载新卷 `pskit-agent-cloud-return-20261003_agent_data:/data`；启动命令只点名 `backend af3-callback-proxy`，无常驻 `web`。

- [ ] **Red:** 写 `test_return_compose_uses_fresh_volume_and_loopback_ports`：用 `docker compose -f compose.yaml -f compose.cloud.yaml -f compose.cloud-return.yaml config --format json` 渲染，断言后端 `/data` 卷名为 `pskit-agent-cloud-return-20261003_agent_data`、旧卷名未出现在任何新服务挂载、后端和代理仅有 `127.0.0.1:18088:8000` 与 `127.0.0.1:18185:8080`；断言固定镜像标签变量必填。
- [ ] **Run red:** `pytest deploy/agent/tests/test_aliyun_return_compose.py -q`，预期因覆盖文件不存在而失败。
- [ ] **Green:** 新增仅声明新卷名和必要云端覆盖值的 Compose 文件，不重写基础服务。保持 `SUPABASE_URL=http://api-gw:8000` 和既有 `pskit-agent-supabase_default` 外部网络；服务端 `MODEL_GATEWAY_*` 由受限环境文件配置。
- [ ] **Verify:** 同一 pytest 与 Compose `config --quiet` 通过；审阅渲染输出，确认 `web` 不在显式启动目标中。
- [ ] **Commit:** 仅提交本任务两文件，`feat(deploy): isolate Aliyun Agent return volume`。

### Task 2: AF3 接收器可切换私网目标

**Files:**
- Modify: `deploy/agent/compose.a6000-receiver.yaml`
- Modify: `deploy/agent/tests/test_a6000_receiver_config.py`

**Interfaces:**
- Consumes: 当前 `af3-receiver` 服务、同一 Compose 项目/服务/容器名、现有 `receiver.cloud.env`、spool 挂载。
- Produces: `AGENT_AF3_API_URL` Compose 变量；未设置为 `http://127.0.0.1:18185`，切换时设置 `http://10.9.8.1:18184`。不改变 worker ID、镜像、UID/GID、计算容器。

- [ ] **Red:** 在现有测试增加 `test_receiver_cloud_url_override_preserves_identity_and_spool`；用 `AGENT_AF3_API_URL=http://10.9.8.1:18184` 渲染并断言命令 URL、worker ID、host 网络和持久挂载；保留默认本机 URL 断言。
- [ ] **Run red:** `pytest deploy/agent/tests/test_a6000_receiver_config.py -q`，预期新覆盖测试失败。
- [ ] **Green:** 将 `--api-url` 的固定值替换为 `${AGENT_AF3_API_URL:-http://127.0.0.1:18185}`，不添加第二个 receiver 服务。
- [ ] **Verify:** 同一 pytest 与两种环境下的 Compose `config --quiet` 均通过。
- [ ] **Commit:** 仅提交本任务两文件，`feat(deploy): parameterize AF3 receiver endpoint`。

### Task 3: 阿里云的两个可回退 Nginx 切换动作

**Files:**
- Create: `deploy/agent/host-nginx-agent-aliyun.conf`
- Create: `deploy/agent/scripts/enable_private_af3_ingress.sh`
- Create: `deploy/agent/scripts/install_host_nginx_agent_cloud.sh`
- Create: `deploy/agent/tests/test_aliyun_return_ingress.py`

**Interfaces:**
- Consumes: 当前公开配置 `host-nginx-agent-split.conf`、原私网 AF3 配置 `host-nginx-af3.conf`、`/etc/nginx/conf.d/agent-af3-private.conf.disabled-20261002`、回环后端 `127.0.0.1:18088`、回环 AF3 代理 `127.0.0.1:18185`。
- Produces: 私网 AF3 入口只在 `10.9.8.1:18184` 接受 `10.9.8.2`；公开 `/api/v1/` 转本机后端并保留静态页、OAuth、SSE、上传和 `/internal/` 404。两个脚本都备份原配置，`nginx -t`、reload、探测失败时恢复原配置；只改 `agent.bioailab.net` 与该 AF3 私网 vhost。

- [ ] **Red:** 写测试断言公开配置的 `/api/v1/` upstream 是 `127.0.0.1:18088`，没有 `10.9.8.2:18088`；`/internal/` 404、精确 OAuth 代理、`proxy_buffering off`、`proxy_request_buffering off`；私网配置保留 `allow 10.9.8.2`/`deny all`；脚本要求 root、备份、预探测、`nginx -t`、失败恢复。
- [ ] **Run red:** `pytest deploy/agent/tests/test_aliyun_return_ingress.py -q`，预期文件缺失失败。
- [ ] **Green:** 从当前 split vhost 派生独立的云端本机配置，保持 React `root /var/www/agent.bioailab.net` 和既有证书；编写两个分步脚本。私网脚本可在公网切换前执行，公网脚本须先收到本机 `/api/v1/usage` 的 401 再切流量。任何失败均恢复已有配置并重新 reload。
- [ ] **Verify:** 同一 pytest 通过；执行 `bash -n` 检查两脚本；用 `nginx -t` 在实际安装阶段确认宿主机完整配置。
- [ ] **Commit:** 仅提交本任务四文件，`feat(deploy): prepare reversible Aliyun ingress cutover`。

### Task 4: 写可执行迁移手册与回退门槛

**Files:**
- Create: `deploy/agent/ALIYUN_RETURN.md`
- Reuse without modifying: `deploy/agent/scripts/agent_data_snapshot.py`

**Interfaces:**
- Consumes: Tasks 1–3 的 Compose/脚本，以及既有 `snapshot(source, destination)`、`restore(snapshot_dir, target)` CLI。
- Produces: 不包含凭据的逐步操作手册：固定镜像跨主机校验传输；备份云端旧卷；查询 Run/AF3 job、receiver journal 与 spool；停止写入；A6000 快照；传输校验；恢复新卷、UID/GID；仅启动云端 `backend af3-callback-proxy`；接收器重指向；私网和公网验收；观察与数据回退。

- [ ] **Draft:** 写入准确的部署目录、卷名、端口和服务名；所有命令避免打印密钥，`umask 077`、归档 SHA-256、逐文件 manifest 和 SQLite integrity check 必须可检查。
- [ ] **Check freeze gate:** 明列 `agent_runs` 非终态、`agent_jobs` 非终态、receiver journal 非空或 spool 有未确认产物时中止切换。停止顺序为 receiver → A6000 新版后端/代理；旧 PSKit 和 AF3 compute 不停止。
- [ ] **Check rollback:** 区分云端启动前、启动后未写入、启动后已写入三个阶段；后一阶段必须先停云端写入、对账、反向快照至**新的 A6000 卷**、验证，再改回 Nginx 和 receiver URL。
- [ ] **Verify:** 对照规格逐节审阅手册，运行 `python -m pytest deploy/agent/tests/test_agent_data_snapshot.py -q` 确认既有快照/恢复工具仍可用。手册中标明真实 AF3 与 Pi 唤醒的验收必须以实际任务记录为证据。
- [ ] **Commit:** 仅提交手册，`docs(deploy): document Aliyun backend return runbook`。

### Task 5: 私网切换、数据迁移与公网切换

**Files:**
- Use: `deploy/agent/ALIYUN_RETURN.md` 和 Tasks 1–3 已审阅文件。
- Record: 在运行手册的部署记录节追加时间、镜像 digest、匿名化状态计数、归档校验及 HTTP/AF3 验收结果；不得写入 key、密码、用户内容或会话文本。

**Interfaces:**
- Consumes: 已通过的配置与脚本、用户现有阿里云 root 会话执行能力。
- Produces: 阿里云唯一新版后端、A6000 唯一 AF3 receiver、正确公网路由和已迁移 Agent 历史。

- [ ] **Preflight:** 在两机重新读 `docker ps`、任务状态、journal、磁盘空间、私网可达性、阿里云 Supabase/LiteLLM 健康及当前 Nginx 配置；任一任务仍执行或数据卷不一致则停止切换并对账。
- [ ] **Prepare:** 只传固定版后端镜像和受限配置；校验镜像 digest/标签、Compose 渲染、回调 key 的一致性和新空卷。不要让云端生产后端提前启动。
- [ ] **Freeze/snapshot:** 按手册停 A6000 receiver 和新版后端；用 `agent_data_snapshot.py` 生成快照，SHA-256 校验后恢复到阿里云新卷；保留两端原卷和归档。
- [ ] **Private verify:** 启云端后端/代理，确认 readiness、已有账号/项目/会话/Pi 历史与文件、Token/GPU 余额、LiteLLM 归属与预算；AF3 入口无 key 404、有效 worker 的 owned-jobs 可读。用户在 root 会话启用私网 AF3 Nginx 后，重启单个 A6000 receiver 指向 `10.9.8.1:18184`，完成受控真实 AF3 领取/产物/ACK/自动唤醒检查。
- [ ] **Public cutover:** 私网验收全通过后，请用户在 root 会话运行已准备的公网 Nginx 切换脚本；验证 HTTPS 登录、SSE、上传、`/internal/` 404、旧站健康；停 A6000 新版 API 代理并确认只有云端新版后端在写入。
- [ ] **Evidence/commit:** 在手册记录不含秘密的验收证据，提交仅该记录；失败时按手册对应阶段回退，不把旧云端卷直接接入生产。

## Plan Self-Review

- 覆盖：目标拓扑对应 Tasks 1–3；数据所有权和回退对应 Task 4；实地切换和验收对应 Task 5。
- 五项 Review Focus 分别由 Task 1、2、3、4/5、4/5 的检查覆盖。
- Task 5 的 root Nginx 命令需要用户在阿里云 root 会话执行；此前所有文件、镜像、快照和私网验收准备由执行者完成。
- A6000 真实 AF3 测试可能有实际 GPU 使用；正式切换时只做一个受控小任务，且先确认当前配额和任务空闲。
