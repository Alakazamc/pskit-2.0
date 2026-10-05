# 新版 PSKit Agent 部署与运行

日期：2026-10-04。适用范围：阿里云生产、阿里云 Staging、A6000 AF3。

本文借鉴 [ASD-STE100 官方 FAQ](https://www.asd-ste100.org/STE_faq.html)的清晰写作原则。每组命令先说明执行主机和条件，再说明预期结果。中文文档不宣称符合英语标准。系统职责见[架构文档](../../docs/AGENT_ARCHITECTURE.md)。

三个 Compose 项目怎样叠加、容器端口怎样映射，见[Compose 与端口说明](COMPOSE_PORTS.md)。

## 1. 部署位置

| 主机 | 服务 | 入口 |
| --- | --- | --- |
| 阿里云宿主机 | Nginx、React `dist` | 普通站点 `https://agent.bioailab.net`；管理入口仅经 WireGuard 访问同一域名的 `/admin/models` |
| 阿里云 Docker | Python/Pi、Supabase、PostgreSQL 17、LiteLLM、AF3 回调代理 | Python `127.0.0.1:18088`；回调代理 `127.0.0.1:18185` |
| 阿里云 WireGuard | AF3 私网 Nginx | `10.9.8.1:18184`，只允许 A6000 `10.9.8.2` |
| A6000 | 一个 AF3 接收器、一个计算容器 | 接收器主动连接阿里云 |
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
VITE_AUTH_MODE=supabase VITE_API_BASE_URL=/api/v1 npm run build
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

管理员页面 `/admin` 和管理 API `/api/v1/admin` 只允许源地址为 WireGuard `10.9.8.0/24` 的连接。修改现有生产配置时，在阿里云 root 会话运行 `bash deploy/agent/scripts/install_private_admin_ingress.sh`；脚本先备份原配置，验证 Nginx 并探测公网拒绝、私网可达，失败则恢复原配置。浏览器需先连接 WireGuard，并在浏览器所在机器将 `agent.bioailab.net` 临时解析到 `10.9.8.1`，这样 HTTPS 证书与域名仍匹配。断开 WireGuard 后移除该临时解析。管理角色还需按 [管理台手册](../../new_backend/ADMIN.md)授予已验证账号；接入私网本身不会授予权限。

## 4. A6000 AF3

执行主机：阿里云 root 会话。执行条件：WireGuard 已提供 `10.9.8.1`，A6000 的地址是 `10.9.8.2`；本机回调代理已健康。在部署仓库根目录安装私网 Nginx 配置：

```bash
install -m 0644 deploy/agent/host-nginx-af3.conf \
  /etc/nginx/conf.d/agent-af3-private.conf
nginx -t
systemctl reload nginx
```

该配置仅在 `10.9.8.1:18184` 监听，并只接受 `10.9.8.2`。

执行主机：A6000。执行条件：接收器的环境文件权限为 `0600`；回调密钥与阿里云一致；持久 spool 目录可写。确保**只有一个**生产接收器在领任务。接收器声明见 [`compose.a6000-receiver.yaml`](compose.a6000-receiver.yaml)。它的镜像、挂载路径和 UID 与这台 A6000 的当前环境绑定；部署到别的 GPU 主机前要核对这些值。

在 A6000 部署仓库根目录运行，先把环境文件路径换成该机器的实际路径：

```bash
AGENT_AF3_API_URL=http://10.9.8.1:18184 \
AGENT_AF3_RECEIVER_ENV_FILE=/path/to/receiver.env \
  docker compose -f deploy/agent/compose.a6000-receiver.yaml \
  --profile cutover up -d af3-receiver
docker compose -f deploy/agent/compose.a6000-receiver.yaml \
  --profile cutover ps
```

接收器主动向阿里云领 Job，再在 A6000 计算。它通过同一私网入口回报进度、上传产物和提交完成状态。不要向公网开放接收器或 AF3 计算容器端口。若接收器重启，保留 journal 与 spool；先核对服务端 Job 状态，再处理未确认结果。

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

预期结果：后端就绪；未登录的 `usage` 返回 `401`；公网 `internal` 返回 `404`；登录页返回 `200`；公网管理页面返回 `403`，WireGuard 管理页面返回 `200`。管理 API 公网返回 `403`，WireGuard 未登录返回 `401`。再用授权账号核对登录、项目、对话、SSE、上传、模型选择和 Token 额度。真实 AF3 要另外检查 Job 领取、进度、产物、GPU 结算与 Pi 自动唤醒。当前生产配置把会员 GPU 日额度设为 `0`；只有明确给测试账号配置额度后，真实 AF3 验收才有意义。不要把只读回调请求当成真实计算成功。

## 7. 停止和恢复

`bash deploy/agent/stack.sh down` 会停止三个生产 Compose 项目，并保留卷。它不负责备份，也不处理 A6000 上未确认的 AF3 回报。停止前先核对运行中的 Run、Job、接收器 journal 和 spool。不要使用 `down -v`。故障恢复后先检查数据库、Pi 会话记录和 AF3 Job 状态，再开放新请求；不要仅根据容器为 `healthy` 就推断用户工作已完成。
