# 阿里云独立部署

## 配置驱动 MCP Tool Product 接收器

通用 MCP receiver 使用现有 Compute Job、租约、配额和回执协议。它不开放端口，也不增加 Nginx 路由；浏览器仍只访问 `/tools/{slug}` 与 `/api/v1/*`。服务地址和凭据只来自已发布 Release 的私有 binding snapshot，以及 receiver 本机的引用映射。

生产 Compose 中 `mcp-receiver` 默认处于 `mcp-receiver` profile，普通 `up -d` 不会启动。Staging overlay 会清除该 profile，让 receiver 默认参与隔离验收。先复制专用配置并限制权限：

```bash
cd deploy/agent
install -m 0600 mcp.receiver.env.example mcp.receiver.env
# 分别填写 worker key、endpoint override、credential ref 和真实 provider token。
docker compose \
  -f compose.yaml -f compose.cloud.yaml -f compose.postgres.yaml -f compose.staging.yaml \
  config --quiet
```

`cloud.backend.env` 中的 `RESEARCH_AGENT_COMPUTE_SERVICE_KEYS_JSON` 必须与 receiver 的 `PSKIT_COMPUTE_SERVICE_KEY` 一致。`PSKIT_MCP_CREDENTIAL_REFS_JSON` 的值是环境变量名，不能直接写 token；endpoint override 只允许把 Release 中的已批准 URI映射到 receiver 可达的固定内网地址。

receiver journal 位于命名卷的 `/var/lib/pskit-mcp/journal.sqlite3`。重启会重发已落盘 outbox；若发现 `executing` 记录则进入 `unknown`，必须人工对账，禁止清空 journal 或盲目重跑。切换生产前必须同时满足：Staging smoke 通过、journal 无 active/unknown、没有尚未 ACK 的任务。

完整 smoke 通过管理员 API执行 probe、discover、保存 draft、qualification、publish，然后以普通用户启动任务、从 cursor 恢复事件、核对 Artifact/usage，最后 suspend 测试 Release。token 建议只通过临时环境变量提供：

```bash
export PSKIT_SMOKE_ADMIN_TOKEN='replace-in-shell-only'
export PSKIT_SMOKE_USER_TOKEN='replace-in-shell-only'
python scripts/tool_product_smoke.py \
  --base-url http://10.9.8.1:18132 \
  --service-id synthetic-mcp \
  --probe-uri https://provider.example/mcp \
  --product-id product-synthetic --slug synthetic --action-id generate \
  --draft /secure/path/synthetic.product.yaml \
  --acceptance /secure/path/synthetic.acceptance.yaml \
  --arguments '{"target":"6FXB"}'
```

当前 CORAL 的一次生成、迭代生成和二维序列分析已有真实证据；口袋分析尚无消费 `af3_ipocket_dis_task_queue` 的 AF3 worker。因此 `coral.acceptance.yaml` 明确设置 `publication.eligible=false`，生产不得导入并发布该 Release，旧 CORAL 页面也暂不删除。

**当前 A6000 迁移目标修订：** 前端改由阿里云宿主机 Nginx 直接服务固定版镜像中提取的 `dist`，新版后端和计算在 A6000，Supabase 仍在阿里云。本文件下面的云端 Web Compose 操作属于原先私网阶段；最终切换及验收以 [A6000_MIGRATION.md](A6000_MIGRATION.md) 为准。

新版使用 `pskit-agent-supabase` 和 `pskit-agent-cloud` 两个 Compose 项目，配置与数据不能复用旧 PSKit 或 WSL 测试环境。云端 Docker 数据目录是 `/data/docker`；数据库、Storage 和 Agent 数据使用独立 Docker 命名卷。不要执行 `docker compose down -v`。

## 私网阶段

将 `infra/supabase`、`deploy/agent` 放在阿里云独立目录。Supabase 从 `.env.example` 创建权限 `0600` 的 `.env`，运行 `utils/generate-keys.sh --update-env` 和 `utils/add-new-auth-keys.sh --update-env` 生成全新密钥；两脚本会打印密钥，执行时须重定向输出。设置 `cloud.env.example` 中的域名、端口和登录开关，保留 `CLOUD_DISABLE_SIGNUP=true`、`ENABLE_EMAIL_AUTOCONFIRM=false`。真实 SMTP 缺失期间只创建管理测试账号，不能开放注册。

应用在 `deploy/agent` 建立权限 `0600` 的 `.env`、`cloud.backend.env`、`cloud.proxy.env`。后两者使用同一枚新生成的 AF3 回调密钥，绝不能复用 WSL 测试密钥。Supabase publishable key 只放 Python 后端配置；service key 留在 Supabase 内。私网阶段使用 `compose.yaml`、`compose.local.yaml`、`compose.cloud.yaml` 顺序叠加并启用 `--profile private-test`，模型替身使用已经传入的后端镜像；正式运行前移除 local overlay 和 private-test profile，配置真实模型网关。默认游客和会员 GPU 额度均为 0。

启动前用 `docker compose config --quiet` 检查配置，核对所有宿主机端口只绑定 `127.0.0.1`。先启动 Supabase，再启动应用。检查 `127.0.0.1:18085`、`127.0.0.1:18088/health/ready`，以及无密钥时 `127.0.0.1:18185` 的拒绝响应。不要将这些端口直接开放到公网。

## 公网域名与邮件：需用户以 sudo 执行

把 `agent.bioailab.net` 的 A 记录指向 `47.121.29.141`，用现有 ACME 工作流签发该域名证书。证书文件存在后，将 [`host-nginx-agent.conf`](host-nginx-agent.conf) 安装为新的虚拟主机；保留旧 `pskit.bioailab.net` 文件：

```bash
sudo install -m 0644 host-nginx-agent.conf /etc/nginx/conf.d/agent.bioailab.net.conf
sudo nginx -t
sudo systemctl reload nginx
curl -I https://agent.bioailab.net/
curl -I https://pskit.bioailab.net/
```

真实 SMTP 主机、端口、用户、密码及发件人填写到云端 Supabase `.env`（权限 `0600`）。同时配置 Turnstile 前端 site key、后端 secret、认证限流 HMAC 和可信代理网段。先以 Nginx dry-run 与 Python observe 运行 24–48 小时并完成 canary，之后才将 `CLOUD_DISABLE_SIGNUP=false` 并验证邮箱验证码全流程；再分步启用 Python 和 Nginx enforcement。回退顺序是 Nginx dry-run、Python observe、关闭注册。Google 登录另需提供者凭据及真实回调验证。公网检查 `/internal/` 为 404、登录 Refresh Cookie 带 `Secure`，并确认旧站仍可访问。本阶段不配置阿里云付费 DDoS/WAF。

## A6000 私网 AF3：需先排查 WireGuard

阿里云为 `10.9.8.1`，A6000 为 `10.9.8.2`。此前双方 WireGuard 地址上的 TCP 超时。两端先运行 `sudo wg show`、`ip route get <对端地址>` 和 `sudo ufw status verbose`（或实际防火墙命令），核对 AllowedIPs、路由和接口。确认云端确有 `10.9.8.1` 后，把 [`host-nginx-af3.conf`](host-nginx-af3.conf) 安装为独立私网监听：

```bash
sudo install -m 0644 host-nginx-af3.conf /etc/nginx/conf.d/agent-af3-private.conf
sudo nginx -t
sudo systemctl reload nginx
sudo ufw allow in on wg0 from 10.9.8.2 to 10.9.8.1 port 18184 proto tcp
```

最后一条只在 UFW 已启用且接口确为 `wg0` 时执行。Nginx 应只在 `10.9.8.1:18184` 接收 `10.9.8.2`；Docker 代理仍只发布 `127.0.0.1:18185`。无需 `10.9.8.3` 转发。

切换前确认旧 WSL 队列和 A6000 receiver journal 无未完成任务，备份原 receiver 配置，只重建 receiver，保留 compute 容器和 spool。新 receiver 使用云端 URL、新回调密钥和 `a6000-af3-cloud-1`。先做带密钥只读 owned-jobs 请求，再用云端测试账号运行一个低成本真 AF3，核对租约、进度、`simulation=false`、产物、GPU 扣费、ACK、spool 清理及 Pi 自动唤醒。失败时保留云端 claim 与 A6000 spool 以便对账，勿把任务重放到旧 WSL 后端。

公开 AF3 前仍须处理运行中任务取消不能立即停 GPU、单件产物 20 MiB 上限。替换镜像或迁移前备份 Supabase DB 卷与 Agent 数据卷；若新 Nginx 配置出错，只删除新的两个配置文件并 `nginx -t`、reload，不动旧站。

## 新版后端迁到 A6000：Agent 数据快照

本节只迁移 `pskit-agent-cloud_agent_data` 中的 `agent.sqlite3`（包括已提交 WAL）和 `pi-sessions/`。Supabase 账号、Postgres、Storage 留在阿里云。以下操作只在确认没有运行中的 Agent Run、AF3 claim，且 A6000 接收器已暂停领任务后执行；先停止阿里云新版后端和回调代理，不能在写入仍进行时复制 Pi 文件。保留源卷与原始归档，禁止 `docker compose down -v`。

阿里云部署目录中执行；这里的 Compose 项目名仍是 `pskit-agent-cloud`，不要停止旧 `pskit` 项目：

```bash
cd /home/ecs-user/pskit-agent-cloud-20261002/deploy/agent
docker compose --env-file .env -f compose.yaml -f compose.cloud.yaml stop af3-callback-proxy backend
umask 077
mkdir -p backups
docker run --rm --network none --user 0:0 \
  -v pskit-agent-cloud_agent_data:/source:ro \
  -v "$PWD/backups":/backup \
  -v "$PWD/scripts/agent_data_snapshot.py":/script.py:ro \
  pskit-agent-backend:cloud-20261002-0ac7743c \
  sh -c 'python /script.py snapshot /source /backup/agent-snapshot --source-stopped && chown -R 1000:1000 /backup/agent-snapshot'
tar -C backups -czf backups/agent-snapshot.tar.gz agent-snapshot
(cd backups && sha256sum agent-snapshot.tar.gz > agent-snapshot.tar.gz.sha256)
```

通过已配置的 SSH 通道将归档和 `.sha256` 从阿里云复制到 A6000 的权限 `0700` 目录。传输过程中不解压到共享目录，也不打印会话内容。在 A6000 部署目录执行；**此时不要启动 A6000 后端**：

```bash
cd /data/jhli/pskit-agent-a6000-20261002/deploy/agent
umask 077
mkdir -p transfer
# 将 agent-snapshot.tar.gz 与 agent-snapshot.tar.gz.sha256 放在 transfer/。
(cd transfer && sha256sum -c agent-snapshot.tar.gz.sha256)
tar -C transfer -xzf transfer/agent-snapshot.tar.gz
docker volume create pskit-agent-a6000_agent_data
docker run --rm --network none --user 0:0 \
  -v pskit-agent-a6000_agent_data:/target \
  -v "$PWD/transfer/agent-snapshot":/snapshot:ro \
  -v "$PWD/scripts/agent_data_snapshot.py":/script.py:ro \
  pskit-agent-backend:cloud-20261002-0ac7743c \
  sh -c 'python /script.py restore /snapshot /target && chown -R 10001:10001 /target'
```

`restore` 会在写入目标卷前验证清单、所有文件 SHA-256 和 SQLite 完整性，并拒绝覆盖非空卷；后端容器使用 UID/GID `10001:10001`。保留云端源卷和归档，直到公网、历史会话及 AF3 回调验收完成。切流量后回退时必须冻结 A6000 写入并迁回**最新** Agent 数据，不能直接重启云端旧后端。

## A6000 AF3 接收器本机回调切换

新接收器定义在 [`compose.a6000-receiver.yaml`](compose.a6000-receiver.yaml)，有显式 `cutover` profile，默认不会启动。它复用当前镜像 `af3_mar5_jhli_2026_0923:v1`、UID/GID `1006:1006`、原 `receiver.cloud.env`、挂载脚本和持久 spool，只把 API 地址改成 `http://127.0.0.1:18185`。现有 `pskit-af3-compute-real-test-20261002` 不归 Compose 管理，也不重建。

先在旧接收器仍运行时检查其 `journal.sqlite3` 的 `jobs` 行数、后端 owned-jobs、spool 中未 ACK 的任务以及计算容器是否仍处理任务。只要任一处有未确认任务，就推迟切换并对账，不能清空 journal 或删除 spool。确认空闲后，在 A6000 执行：

```bash
cd /data/jhli/pskit-agent-a6000-20261002/deploy/agent
umask 077
docker inspect pskit-af3-receiver-cloud-20261002 > receiver-before-local-cutover.json
docker stop pskit-af3-receiver-cloud-20261002
docker inspect pskit-af3-receiver-cloud-20261002 --format '{{.State.Running}}'
docker compose -f compose.a6000-receiver.yaml --profile cutover config --quiet
docker compose -f compose.a6000-receiver.yaml --profile cutover up -d af3-receiver
docker ps --filter name=pskit-af3-receiver --format '{{.Names}} {{.Status}}'
```

`receiver-before-local-cutover.json` 含有容器环境变量，文件必须保持 `0600`，也不能提交或打印。旧接收器应显示 `false`，列表中只允许一个运行中的新版 receiver；计算容器应持续运行。新接收器连接本机回调代理，先验证无密钥 404、带密钥的只读 owned-jobs，再执行获批的单个真 AF3 任务。验收前保留旧容器和原 spool/journal；失败时先停止新接收器并核对是否已有 claim，再决定恢复旧入口，不能同时运行两个接收器。

## 2026-10-02 私网部署记录

- 阿里云目录：`/home/ecs-user/pskit-agent-cloud-20261002`。旧 `pskit` 容器与 `pskit.bioailab.net` 未修改；部署后旧站 HTTPS 返回 200。
- 后端镜像 ID：`sha256:0ac7743c3c36277ea8e6dd944bd637c4e7612d66d3d08f2a6db088bb595000a6`；前端镜像 ID：`sha256:44879324a06d6ac90560dca3663f394fb0390612cf458400c22f839159e70f89`。
- Agent 镜像传输包 SHA-256：`ed1a00a99f163f52305c62f74fce21e1308c696f6e5b36e83b9ff9d706c0d424`；12 个固定版 Supabase 镜像包 SHA-256：`6eee3f2eebf8ce8f4c1865a29536dd22541060a88261013de0231c2a97deeef2`。阿里云连接 Docker Hub 超时，所以镜像从本机离线传入；压缩包暂存云端部署目录，保留作同版本恢复材料。
- Docker 数据卷：`pskit-agent-db-data`、`pskit-agent-storage`、`pskit-agent-supabase_db-config`、`pskit-agent-cloud_agent_data`。它们位于阿里云 Docker 数据根目录 `/data/docker`，不在旧 PSKit 数据路径下。
- 私网检查已通过：全套容器健康；仅 `127.0.0.1:18130/18085/18088/18185` 发布；Python 登录、Secure/HttpOnly Cookie、GPU 默认额度 0、项目、Pi 回复、SSE、上传均通过。后端与代理重启后，账号、项目和上传文件仍在。AF3 代理无密钥返回 404，带新密钥的只读 owned-jobs 返回空列表。
- 私网测试账号保存在云端权限 `0600` 的 `private-test-account.json`，聊天和仓库均没有保存密码。DNS 已指向阿里云；公开 TLS 和真实 SMTP 尚待完成。
- 阿里云私网 Nginx 已监听 `10.9.8.1:18184`，配置仅允许 `10.9.8.2`。阿里云本机访问返回 403；A6000 无密钥访问返回 404，带新密钥读取 `a6000-af3-cloud-1` 的 owned-jobs 返回空列表，证明 WireGuard、Nginx、回调代理与鉴权链路可达。
- A6000 已停止旧 `pskit-af3-receiver-real-test-20261002`，启动 `pskit-af3-receiver-cloud-20261002` 连接 `http://10.9.8.1:18184`；`pskit-af3-compute-real-test-20261002` 持续运行，原 spool 未改动。旧配置和 journal 已分别备份为 `receiver.env.pre-cloud-20261002`、`spool/journal.pre-cloud-20261002.sqlite3`（均为 `0600`）；新密钥在 `receiver.cloud.env`（`0600`）。切换时旧 journal 与旧后端 owned-jobs 均为 0，新接收器运行后 journal 仍为 0；两个已有 outcome 的旧 spool 目录未删。云端测试账号的 GPU 每日额度仍为 0，真实 AF3 提交和 Pi 自动唤醒尚待验证。
- 初始备份位于云端 `backups/private-20261002/`（目录 `0700`、文件 `0600`），包括 Postgres SQL、Agent SQLite、Pi 会话、Storage 卷和数据库加密配置卷；五份备份已通过读取、压缩格式及 SQLite 完整性检查。这是同机备份，正式开放前仍需异地备份策略。
