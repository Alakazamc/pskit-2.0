# 新版 PSKit Agent 部署与运行

日期：2026-10-07。适用范围：阿里云生产、阿里云 Staging、A6000 通用 MCP/AF3 接收器。

本文借鉴 [ASD-STE100 官方 FAQ](https://www.asd-ste100.org/STE_faq.html)的清晰写作原则。每组命令先说明执行主机和条件，再说明预期结果。中文文档不宣称符合英语标准。系统职责见[架构文档](../../docs/AGENT_ARCHITECTURE.md)。

三个 Compose 项目怎样叠加、容器端口怎样映射，见[Compose 与端口说明](COMPOSE_PORTS.md)。

## 1. 部署位置

| 主机 | 服务 | 入口 |
| --- | --- | --- |
| 阿里云宿主机 | Nginx、React `dist` | 普通站点 `https://agent.bioailab.net`；管理入口仅经 WireGuard 访问同一域名的 `/admin/models` |
| 阿里云 Docker | Python/Pi、Supabase、PostgreSQL 17、LiteLLM、AF3 回调代理 | Python `127.0.0.1:18088`；回调代理 `127.0.0.1:18185` |
| 阿里云 WireGuard | AF3 私网 Nginx | `10.9.8.1:18184`，只允许 A6000 `10.9.8.2` |
| 阿里云 WireGuard | 通用计算私网 Caddy | `10.9.8.1:18186`，只允许 A6000 `10.9.8.2` |
| A6000 | 一个通用 MCP/AF3 接收器、一个独立 AF3 计算容器 | 接收器主动连接阿里云；CORAL 调用 4090 MCP |
| 4090 | CORAL MCP 模型服务 | 由已审核的服务绑定选择，不向浏览器暴露服务端凭据 |
| 阿里云 Staging | 独立数据库、模型替身、AF3 替身、前端 `dist` | `10.9.8.1:18132`，仅私网 |

生产前端由 Nginx 提供静态文件。生产 Agent 没有前端容器。`stack.sh` 是统一操作入口；它调用 Supabase、LiteLLM 和 Agent 三个 Compose 项目。三个项目共享一台 PostgreSQL 17 容器，但使用不同的逻辑库或 schema 与账号。

## 2. 生产准备

执行主机：阿里云。执行条件：Docker、Compose、Nginx、TLS 证书和 WireGuard 已安装；镜像使用固定版本；服务器有可恢复备份。

准备以下私有文件。所有文件权限为 `0600`。不要把文件内容打印到终端记录或提交到 Git。

公开的源码脚本使用 `0644`，尤其是会直接挂载给容器的 `deploy/agent/scripts/provision_shared_postgres.py` 和 `deploy/agent/tests/mock_model_gateway.py`；后端镜像中的 `agent` 是 UID 10001，需要读取这些文件。不要把私有配置的 `0600` 权限统一套在公开源码上。Staging 启动中途失败后，先按手册停止其专属项目并保留卷，再重新启动，避免部分启动的服务占用预检端口。

| 文件 | 作用 |
| --- | --- |
| `infra/supabase/.env` | Supabase、PostgreSQL 和邮件配置 |
| `infra/litellm/.env.shared` | LiteLLM 数据库账号、master/salt 与监听地址 |
| `deploy/agent/cloud.env` | 固定后端镜像、公共站点地址和 Agent 数据卷 |
| `deploy/agent/cloud.backend.env` | `pskit_app` DSN、Supabase 公钥、LiteLLM 虚拟 key、AF3 回调密钥 |
| `deploy/agent/cloud.proxy.env` | AF3 回调代理的密钥 |
| `deploy/agent/.env.stack-admin` | 数据库角色配置；仅供启动脚本的管理步骤使用 |

`cloud.backend.env` 的模型别名必须存在于 LiteLLM。提供商 API key 留在 LiteLLM；React 构建不得包含这些 key。`cloud.env` 中的 `AGENT_BACKEND_IMAGE` 必须指向本次固定镜像。示例字段见 [`cloud.backend.env.example`](cloud.backend.env.example)与[`cloud.env.example`](cloud.env.example)。

在阿里云仓库根目录运行：

```bash
bash deploy/agent/stack.sh up
bash deploy/agent/stack.sh status
```

`up` 依次等待 Supabase、检查数据库角色、启动 LiteLLM、准备 `pskit` schema，最后启动 Python 和 AF3 回调代理。`status` 应显示一个 Supabase PostgreSQL 容器，且 LiteLLM 没有独立 PostgreSQL 容器。预检失败时，先看对应日志；保留数据卷。

```bash
bash deploy/agent/stack.sh logs supabase
bash deploy/agent/stack.sh logs litellm
bash deploy/agent/stack.sh logs agent
```

## 3. 前端和 Nginx

执行主机：阿里云。执行条件：`new_frontend` 的依赖已锁定；`agent.bioailab.net` 的 TLS 证书可用。

在仓库根目录生成前端静态文件：

```bash
cd new_frontend
npm ci
VITE_AUTH_MODE=supabase VITE_API_BASE_URL=/api/v1 \
VITE_TURNSTILE_SITE_KEY='<public-site-key>' npm run build
```

产物是 `new_frontend/dist`。由有权限的运维进程把本次**完整且已核对**的产物发布到 `/var/www/agent.bioailab.net`。生产 Nginx 使用 [`host-nginx-agent-aliyun.conf`](host-nginx-agent-aliyun.conf)：`/api/v1/` 转到本机 Python，`/internal/` 返回 404，其余路径由 SPA 处理。发布静态文件时无需启动 `web` 容器。

首次安装或修改 Nginx 配置时，在阿里云 root 会话切换到部署仓库根目录，再运行：

```bash
install -m 0644 deploy/agent/host-nginx-agent-aliyun.conf \
  /etc/nginx/conf.d/agent.bioailab.net.conf
nginx -t
systemctl reload nginx
```

只有 `nginx -t` 成功才 reload。日常仅更新后端镜像或 React `dist` 时，不需要重新配置域名、证书或 Nginx 路由。

### 公网认证防滥用

认证入口使用三层保护：Nginx 只限制注册、找回密码、游客升级、登录和验证码确认；Python 使用 PostgreSQL 原子计数器执行用户、IP 与邮箱桶；GoTrue 继续执行邮件冷却与内部限额。Nginx 必须覆盖客户端提交的 `X-PSKit-Client-IP`，Python 只信任 `cloud.backend.env` 中声明的代理网段。日志只记录请求 ID、状态码和 `$limit_req_status`，不得记录邮箱、验证码、Cookie、Token 或原始 IP。

首次发布保持 `limit_req_dry_run on`、`RESEARCH_AGENT_AUTH_ABUSE_MODE=observe` 和 `CLOUD_DISABLE_SIGNUP=true`。先配置真实 SMTP、Turnstile site key、server secret、独立 HMAC secret 与可信代理网段；再用 Staging 和受控账号验证注册、重发、找回、游客升级及错误恢复。观察 24–48 小时的聚合指标后，先把 Python 切到 `enforce`，再把三个 vhost 中的 `limit_req_dry_run` 改为 `off`。每次修改都必须先运行 `nginx -t`，失败时恢复安装脚本留下的备份。

上述保护通过 canary 后，才把 Supabase 私有环境中的 `CLOUD_DISABLE_SIGNUP` 与 `DISABLE_SIGNUP` 改为 `false`。发生误拦截时，先把 Nginx 恢复为 dry-run，再把 Python 恢复为 observe；不要放宽普通 API、SSE、上传和管理入口。此阶段不启用阿里云付费 DDoS/WAF。

管理员页面 `/admin` 和管理 API `/api/v1/admin` 只允许源地址为 WireGuard `10.9.8.0/24` 的连接。修改现有生产配置时，在阿里云 root 会话运行 `bash deploy/agent/scripts/install_private_admin_ingress.sh`；脚本先备份原配置，验证 Nginx 并探测公网拒绝、私网可达，失败则恢复原配置。浏览器需先连接 WireGuard，并在浏览器所在机器将 `agent.bioailab.net` 临时解析到 `10.9.8.1`，这样 HTTPS 证书与域名仍匹配。断开 WireGuard 后移除该临时解析。管理角色还需按 [管理台手册](../../new_backend/ADMIN.md)授予已验证账号；接入私网本身不会授予权限。

## 4. A6000 通用 MCP 与 AF3

A6000 使用一个 `pskit-mcp-receiver-a6000` 容器、一个 Python 进程接收 CORAL 和 AF3。CORAL 调用 4090 的已审核 MCP 服务；AF3 的 `af3.submit` / `af3.status` 在同一接收器进程内仅监听 `127.0.0.1:18187/mcp`。独立的 AF3 模型计算容器读取持久 spool；接收器重启不停止正在运行的计算。

### 阿里云私网控制入口

既有 AF3 兼容入口 `10.9.8.1:18184` 保留历史任务、GPU 结算和 Pi 唤醒。通用计算入口 `10.9.8.1:18186` 由固定版本 Caddy 提供，只允许源地址 `10.9.8.2` 的 claim、heartbeat、result。后端仍验证服务密钥和执行租约。该入口没有公网或通用管理 API 路由。

AF3 私网代理还需放行带密钥的 `GET /internal/compute/af3/jobs/:jobId`，用于确认终态与晚到用量结算；不能将全部 internal API 开放。云端 Compose 将 `AGENT_AF3_PROXY_SCRIPT` 指向本次已校验的不可变代理源码，默认使用仓库脚本，只读挂载到代理容器。修改白名单时只更新代理；无需重启后端或模型容器。

执行主机：阿里云。后端及 `pskit-agent-cloud_app` 网络就绪后，在部署仓库根目录运行：

```bash
docker compose -f deploy/agent/compose.compute-ingress.yaml up -d --wait
docker compose -f deploy/agent/compose.compute-ingress.yaml ps
```

此独立项目加入既有应用网络，不重建数据库或 Agent。不要修改宿主 Nginx 来重复开放 internal 路由。

### A6000 接收器

声明见 [`compose.a6000-mcp-receiver.yaml`](compose.a6000-mcp-receiver.yaml)。先核对固定运行镜像的 MCP 依赖，制作已提交源码的不可变快照，并在隔离容器执行真实 MCP 协议 smoke。私有接收器环境文件必须为 `0600`，包含 `PSKIT_COMPUTE_SERVICE_KEY`、既有 `RESEARCH_AGENT_COMPUTE_CALLBACK_KEY`；凭据留在服务端。其他 provider credential ref 按服务配置提供。

Compose 环境文件声明以下非秘密路径和固定镜像：

```ini
AGENT_MCP_RECEIVER_IMAGE=sha256:<verified-image-id>
AGENT_MCP_RECEIVER_RUNTIME=/data/jhli/pskit-mcp-receiver-20261007/runtime-<commit>
AGENT_MCP_RECEIVER_ENV_FILE=/data/jhli/pskit-mcp-receiver-20261007/receiver.env
AGENT_MCP_RECEIVER_JOURNAL=/data/jhli/pskit-mcp-receiver-20261007/journal
AGENT_MCP_RECEIVER_UID=1006
AGENT_MCP_RECEIVER_GID=1006
AGENT_AF3_RECEIVER_SPOOL=/data/jhli/pskit-af3-receiver-test-20261002/spool
```

UID/GID 必须匹配既有 AF3 spool。通用和 AF3 journal 分别使用 `coral.sqlite3`、`af3.sqlite3`；任何未确认记录都保留。切换前核对服务端活动任务及旧 journal，停止旧 AF3 接收器，保留模型计算容器，再运行：

```bash
docker compose --env-file /path/to/compose.env \
  -f deploy/agent/compose.a6000-mcp-receiver.yaml up -d --wait
docker logs --tail 30 pskit-mcp-receiver-a6000
```

必须确认两个循环均正常工作，且只有一个接收器领取生产 AF3。旧 [`compose.a6000-receiver.yaml`](compose.a6000-receiver.yaml) 只供经过 journal/owned 核对后的恢复使用，不能与通用接收器同时领任务。

AF3 计算完成后，结果与产物先进入 outbox；收到且验证服务端提交回执后才清理 journal，再删除已确认的 spool。用户取消或任务超时后，已开始的计算继续报告晚到用量，确认 `reconciled` 后才清理，不重新打开任务。CORAL 的不确定结果保留为 `unknown`，只暂停该服务，AF3 循环继续运行。

AF3 旧计算器只有取整 GPU 分钟，映射为 `gpu_device_ms` 时标为 `estimated`；缺失的 CPU/wall 耗时保持空值。不能把它当作 CUDA kernel 的实测耗时。

## 5. Staging

执行主机：阿里云。执行条件：固定后端镜像和前端 `dist` 已准备；生产私有文件仍可读，以便检查 Staging 密钥没有复用生产密钥。Staging 使用独立项目名、网络、卷和账号。它使用模型替身和 AF3 替身，不连接 A6000。

**首次创建** Staging 配置时，在仓库根目录运行。配置生成器拒绝覆盖已有目录：

```bash
python deploy/agent/scripts/prepare_staging.py \
  /home/ecs-user/pskit-agent-staging-private \
  --backend-image 'pskit-agent-backend:<固定发布标签>' \
  --frontend-dist /path/to/new_frontend/dist
STAGING_CONFIG_DIR=/home/ecs-user/pskit-agent-staging-private \
  bash deploy/agent/staging.sh up
STAGING_CONFIG_DIR=/home/ecs-user/pskit-agent-staging-private \
  bash deploy/agent/staging.sh status
```

首次提供私网站点时，由阿里云 root 执行 [`install_host_nginx_staging.sh`](scripts/install_host_nginx_staging.sh)。该脚本检查 `dist` 清单、WireGuard 地址与 Nginx 配置。访问 `http://10.9.8.1:18132/` 检查站点。后续发布保留现有私有配置与测试卷，停止 Staging 项目后用 [`pin_staging_release.py`](scripts/pin_staging_release.py) 更新制品锁定信息，再按 [Staging 手册](STAGING.md)启动验收；不重复生成密钥或迁移测试数据。

可复用的日常发布流程在 [`pskit-cloud-deploy` skill](../../skills/pskit-cloud-deploy/SKILL.md)。它包含 WSL/Windows SSH、制品校验、定向更新后端、静态文件发布与回滚。现有静态 root 可写且 Nginx 路由不变时，无需重新运行 root 安装脚本。

## 6. 上线检查

执行主机：阿里云。以下请求用于确认入口和权限边界：

```bash
curl --fail --silent --show-error http://127.0.0.1:18088/health/ready
curl --silent --show-error --output /dev/null --write-out '%{http_code}\n' \
  https://agent.bioailab.net/api/v1/usage
curl --silent --show-error --output /dev/null --write-out '%{http_code}\n' \
  https://agent.bioailab.net/internal/
curl --head --fail --silent --show-error https://agent.bioailab.net/login
curl --noproxy '*' --silent --show-error --output /dev/null --write-out '%{http_code}\n' \
  https://agent.bioailab.net/admin/models
curl --noproxy '*' --resolve agent.bioailab.net:443:10.9.8.1 \
  --silent --show-error --output /dev/null --write-out '%{http_code}\n' \
  https://agent.bioailab.net/admin/models
```

预期结果：后端就绪；未登录的 `usage` 返回 `401`；公网 `internal` 返回 `404`；登录页返回 `200`；公网管理页面返回 `403`，WireGuard 管理页面返回 `200`。管理 API 公网返回 `403`，WireGuard 未登录返回 `401`。再用授权账号核对登录、项目、对话、SSE、上传、模型选择和 Token 额度。工具发布还需断言已批准的 slug（例如 `coral`）出现在 `/api/v1/tool-products`，打开配置 UI 并提交受限任务；目录 200 或应用代码存在不代表工具已开放。真实 AF3 要另外检查 Job 领取、进度、产物、GPU 结算与 Pi 自动唤醒。当前生产配置把会员 GPU 日额度设为 `0`；只有明确给测试账号配置额度后，真实 AF3 验收才有意义。不要把只读回调请求当成真实计算成功。

## 7. 停止和恢复

`bash deploy/agent/stack.sh down` 会停止三个生产 Compose 项目，并保留卷。它不负责备份，也不处理 A6000 上未确认的 AF3 回报。停止前先核对运行中的 Run、Job、接收器 journal 和 spool。不要使用 `down -v`。故障恢复后先检查数据库、Pi 会话记录和 AF3 Job 状态，再开放新请求；不要仅根据容器为 `healthy` 就推断用户工作已完成。
