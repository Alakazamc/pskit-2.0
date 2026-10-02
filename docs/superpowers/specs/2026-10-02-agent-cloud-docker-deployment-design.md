# 新版 Research Agent 本地验证与阿里云 Docker 部署设计

## 目标与边界

新版 `new_frontend`、`new_backend` 和独立的 Supabase 身份服务最终运行在现有阿里云服务器，通过 `https://agent.bioailab.net` 提供服务。A6000 上的常驻 AF3 接收器继续主动领取任务，使用 WireGuard 私网连接云端。旧 `pskit.bioailab.net`、旧容器和 A6000 计算容器保持原状。

用户要求**先在本地验证 Docker 部署，再决定何时在阿里云执行**。阿里云不迁移当前 WSL 的测试账号、Supabase 数据或 Agent 会话，使用全新数据和密钥。真实 SMTP 配置稍后由用户提供。在本地验证完成并得到用户继续上云的指示前，不在阿里云安装或启动新版服务。

## 已确认的环境事实

- SSH 别名 `aliyun` 对应的云服务器 WireGuard 地址为 `10.9.8.1`；A6000 为 `10.9.8.2`。`10.9.8.3` 不参与本方案。
- 云服务器有 Docker Compose、14 GiB 内存、约 32 GiB 根分区剩余空间，Nginx 已占用公网 80/443；`ecs-user` 当前不能免密执行 sudo。旧 PSKit 容器监听云端回环端口 `10706`，旧域名的 Nginx 配置仍指向 A6000 的 `10.9.8.2:10716`。
- 新域名 `agent.bioailab.net` 尚未解析，也没有对应的 Nginx 虚拟主机和证书。
- 云端与 A6000 的 `wg0` 都处于 active，但测试中双方 WireGuard 地址上的 TCP 端口均超时；云端仍能经 WireGuard 路由连接 A6000 的 `172.31.199.38:22`。这说明需要进一步检查地址、AllowedIPs 和主机防火墙，不能据此断定只需开放一个应用端口。
- 当前 WSL 的 Supabase 固定快照为 `self-hosted/v0.8.2`，网关只发布在本机回环地址，邮件经 Mailpit 接收。当前 live Python/Pi 和 A6000 AF3 的真实小任务已分别验证；它们不是云端部署的替代品。

## 方案选择

采用**云端独立 Docker 栈 + A6000 私网主动拉取**。备选的 WSL→云端 SSH 转发会使正式服务依赖用户电脑持续在线，因此只保留现有 WSL 链路作为本地开发环境。也不把 A6000 的 AF3 计算进程或内部回调接口暴露到公网。

## 组件与数据流

```text
浏览器 ─HTTPS─> 云端 Nginx agent.bioailab.net
                    └─127.0.0.1:18085─> Docker web（React 静态文件、/api/v1 代理）
                                               └─Docker 内网─> Python/Pi backend
                                                                    ├─独立 SQLite/Pi 持久卷
                                                                    └─Supabase api-gw 内网

A6000 receiver ─WireGuard─> 云端 Nginx 10.9.8.1:18184
                                  └─127.0.0.1:18185─> 受限 AF3 callback proxy
                                                           └─Docker 内网─> Python backend
A6000 compute ─本地持久 spool─> receiver
```

`deploy/agent/compose.yaml` 管理新版 `web`、`backend` 和 `af3-callback-proxy`。后端镜像包含 Python 3.12、固定版本 Node、`pi/package-lock.json` 中锁定的 Pi CLI；前端使用构建产物和固定版本的静态 Web 镜像。基础镜像固定到明确版本或 digest，不用 `latest`。后端 SQLite 与 Pi 会话目录挂到持久卷，容器重建后保留。应用代码、构建产物和密钥分开交付，密钥不写进镜像、Git 或前端 `VITE_*` 变量。

固定版本 Supabase 快照仍位于 `infra/supabase/`，云端用独立 Compose 项目、新数据库卷和新密钥。它的网关及管理端口只绑定云端回环地址，Python 后端经 Docker 内网访问 `api-gw:8000`；浏览器的邮箱认证请求只调用 Python。当前代码把同一个 `SUPABASE_URL` 同时用于 Python 内部请求与 Google OAuth 的浏览器跳转，因此部署前要拆成内部 URL 和公开 OAuth URL。Google 登录需要的 Supabase 授权及回调路径由 Web 代理按路径单独暴露，其他 Supabase 管理/REST/Studio 路径保持关闭；没有 Google 提供者凭据时不宣称 Google 登录已联通。云端邮箱验证码使用真实 SMTP，Mailpit 仅用于本地验证。云端应用的公开 API URL、前端 URL 均设置为 `https://agent.bioailab.net`，Refresh Cookie 设置 `Secure`。

公网 Nginx 只转发 `agent.bioailab.net` 到云端回环地址 `127.0.0.1:18085`。`web` 容器为 SPA 路由返回 `index.html`，把 `/api/v1/` 转给 `backend`，按需把 Google OAuth 的 `/auth/v1/authorize` 与 `/auth/v1/callback` 转给 Supabase，支持 SSE 长连接与现有文件上传上限，并拒绝 `/internal/` 及其他 Supabase 路径。后端本身仅通过 Docker 内网及诊断用的回环端口 `127.0.0.1:18088` 可达。旧域名和旧 Nginx 配置不修改。

AF3 代理容器只发布到云端回环地址 `127.0.0.1:18185`，校验回调密钥、指定 worker ID、HTTP 方法及路径；后端继续校验租约与任务归属。宿主机 Nginx 单独监听 `10.9.8.1:18184`，只允许来源 `10.9.8.2`，并转发到回环代理；云端主机防火墙也只允许该来源访问此端口。[Docker 官方文档](https://docs.docker.com/engine/network/packet-filtering-firewalls/#docker-and-ufw)说明，直接发布容器端口时流量可能绕过 UFW，因此不能仅靠 UFW 保护发布到 `wg0` 的容器端口。A6000 的 receiver 改为访问 `http://10.9.8.1:18184`，使用新的云端回调密钥；compute 容器、GPU 设备和持久 spool 不变。Worker 主动连接云端，不需要给 A6000 做校园网入站端口转发。

## 本地优先验证

1. 在仓库新增 Docker 构建和 Compose 文件；本地使用 `127.0.0.1:18085`（网页）、`127.0.0.1:18088`（后端诊断）和 `127.0.0.1:18185`（AF3 代理测试），避免与现有 18080/18084 服务冲突。
2. 本地 Compose 后端加入现有 Supabase 的 `pskit-supabase_default` 网络，使用其 `api-gw:8000`；继续使用现有本地身份服务，但给 Docker 后端使用独立的 SQLite/Pi 测试卷。云端则连接自己的新 Supabase 网络和全新数据卷。
3. 先验证登录、项目与聊天、SSE、文件上传、Token/GPU 额度及本地 mock AF3 回调；再验证代理拒绝无密钥、错误 worker、非 AF3 路径和超大请求。测试先于相应实现，保持现有 Python 与前端契约。
4. 本地验证结果和镜像版本记录到部署说明。用户确认继续上云前，只在本地运行这些容器，不改动云端 Nginx、DNS 或运行中的旧容器。

## 云端上线与人工操作

本地验证通过后，先在云端以独立目录和 Compose 项目部署固定版本 Supabase、新版应用及持久卷，完成回环端口健康检查。只有真实 SMTP 和新域名准备好后才开放用户登录。需要用户或具备 sudo 权限的管理员：

1. 将 `agent.bioailab.net` 的 DNS 指向当前阿里云公网地址，签发该域名 HTTPS 证书，并安装经仓库审阅过的独立 Nginx 虚拟主机配置。
2. 提供真实 SMTP 参数，通过权限为 `0600` 的云端配置文件注入；不把参数发到 Git 或前端。
3. 检查两端 `sudo wg show`、`sudo ufw status verbose` 和 WireGuard 地址的 TCP 连通性。连通后让宿主机 Nginx 只在 `10.9.8.1:18184` 接收 `10.9.8.2`，并在云端 UFW 允许同一方向；Docker 代理容器只绑定 `127.0.0.1:18185`，不直接发布到 `wg0` 或公网。如 A6000 出站规则限制连接，仅放行该方向。无需操作 `10.9.8.3`。

云端 API 和 AF3 私网代理通过检查后，先确认旧 WSL 队列与 A6000 本地 journal 没有待完成任务，再切换 A6000 receiver 的 API 地址和密钥。切换只重启 receiver，不能中断 compute 容器。用一个低成本真实 AF3 任务核对 `simulation=false`、产物下载、GPU 结算和 Pi 后台唤醒；若失败，先保留云端任务与本地 spool 对账，不能把已领取云端任务直接重放到旧 WSL 后端。

## 故障边界与回退

- 接收器或 WireGuard 暂时断开时，A6000 spool/journal 保存未确认产物，后端保留任务；恢复后按同一任务和租约重试。计算容器崩溃后从输入重跑，AF3 内部检查点恢复不在本次范围。
- 云端 Nginx、后端、Supabase、数据库卷分别有健康检查和持久化备份；部署不删除旧 PSKit、旧 Supabase 或 WSL 数据。新站点未通过验证时保持独立、不切换旧域名。
- 用户取消已经执行的 AF3 任务目前不会立即停止 GPU 进程；大产物仍受当前 20 MiB 单件上限约束。这两项须在正式向普通用户开放 AF3 前解决或明确限制使用范围。
- 因当前 `ecs-user` 没有免密 sudo，DNS、证书、Nginx 和 UFW 操作需要用户/管理员执行。需要的端口与命令应在本地 Docker 验证后交付为可复制的操作清单，而不是提前盲目开放端口。
