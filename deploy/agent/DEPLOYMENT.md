# 新版 PSKit Agent 部署与运行

> 用户工作区的 OpenSandbox/gVisor 叠加、固定镜像、配额卷和失败关闭预检见 [OPENSANDBOX.md](OPENSANDBOX.md)。该叠加通过 Staging live probe 前，生产继续使用 `RESEARCH_AGENT_WORKSPACE_PROVIDER=disabled`；禁止以 `runc`、普通 named volume 或本地子进程代替。

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
| A6000 WireGuard | 已审核的 AF3 MCP | `10.9.8.2:18187/mcp`，必须使用服务端 Bearer 凭据 |
| 4090 | CORAL MCP 模型服务 | 由已审核的服务绑定选择，不向浏览器暴露服务端凭据 |

## OpenSandbox 生产灰度

生产只接受 Staging 生成的 `status=qualified` 私有 manifest。生产
`cloud.env` 使用相同 backend、OpenSandbox Server 和 workspace 镜像 digest，
但必须配置不同的 namespace、API key、runtime network、state volume 和 rollout
目录。`opensandbox.private.toml` 的 execd/egress digest 与 Staging manifest 保持
一致。首次只对一个明确的管理员 user ID 开文件能力：

```bash
bash deploy/agent/scripts/deploy_opensandbox_release.sh \
  --manifest /home/ecs-user/pskit-agent-staging-private/opensandbox-release.json \
  --allow-user '<Supabase user UUID>'
```

脚本依次执行静态预检、兼容数据库迁移、unknown/active attempt 检查、启动私有
OpenSandbox Server、真实 gVisor capability probe、哈希核对、原子写入 files-only
rollout，再只重建 backend。它不会重建 Supabase、LiteLLM、AF3 proxy、CORAL
receiver 或前端。命令和 Python 仍保持关闭；确认文件工具稳定后，运维人员才用
`set_workspace_rollout.py` 写入同一 capability hash 并显式增加
`--commands-enabled`。

常规 `stack.sh` 要沿用 OpenSandbox overlay 时，在权限 0600 的 `.env.stack`
设置 `STACK_OPENSANDBOX_ENABLED=true`。该开关只决定 Compose 叠加；真正用户
授权仍来自服务端 rollout `policy.json`，浏览器不能开启。

回退先关闭新调用并等待活动 attempt 排空，再让 backend 回到 provider disabled
配置，最后停止 Server：

```bash
bash deploy/agent/scripts/rollback_opensandbox_release.sh --drain-seconds 120
```

发现 `unknown` attempt 时脚本拒绝停止 provider，必须先人工确认进程终止并结算。
回退不使用 `down -v`，不删除 OpenSandbox state、用户卷、Artifact、对话或数据库
记录，也不恢复 raw Pi built-ins。AF3/CORAL 不受该脚本影响。
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

通用计算还需要 CPU 配额。在实际 `AGENT_BACKEND_ENV_FILE` 指向的文件中设置 `RESEARCH_AGENT_COMPUTE_CPU_DAILY_LIMIT_MS=3600000`，即每天 1 CPU 核小时；计量日按 UTC 划分。该值是未单独设置 CPU 配额的用户默认值，用户级设置优先；GPU 配额独立计算。缺少此字段时后端默认 CPU 额度为 0，CORAL 等需要预留 CPU 的任务会返回 `CPU_QUOTA_EXCEEDED`（HTTP 429）。修改环境文件后，定向重建后端容器，使环境变量生效。

GPU 默认额度使用 `RESEARCH_AGENT_MEMBER_DAILY_GPU_MINUTES=60`（正式用户每天 60 GPU 设备分钟）和 `RESEARCH_AGENT_GUEST_DAILY_GPU_MINUTES=0`（游客不可执行 GPU 任务）。个人 `agent_gpu_limits` 设置优先，显式 0 仍会禁止该账号使用 GPU。旧部署的私网验证阶段曾把会员默认值设成 0；发布真实计算时必须检查实际后端环境文件和已运行容器，不能只检查代码默认值。CORAL 一次生成需预留 120,000 GPU 设备毫秒，即 2 分钟；设置 0 会返回 `GPU_QUOTA_EXCEEDED`，即使没有历史用量也一样。诊断必须沿用应用的 `PersistentConversationStore._gpu_limit_for` 身份策略，不能给独立 `ComputeLedger` 人为指定 60 分钟来判断生产准入。

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

### 后端镜像构建源

后端 Dockerfile 的 Node 与 Python 基础镜像仍默认锁定版本和 digest。网络正常时直接构建：

```bash
docker build --label org.opencontainers.image.revision="$COMMIT" \
  -f deploy/agent/backend.Dockerfile -t "$IMAGE" new_backend
```

WSL NAT 访问 Docker Hub、PyPI 或 npm 较慢时，可以只替换下载入口，不改变依赖版本：

```bash
docker build --label org.opencontainers.image.revision="$COMMIT" \
  --build-arg PYPI_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
  --build-arg NPM_REGISTRY=https://registry.npmmirror.com \
  --build-arg NODE_BASE_IMAGE="$PSKIT_NODE_BASE_IMAGE" \
  --build-arg PYTHON_BASE_IMAGE="$PSKIT_PYTHON_BASE_IMAGE" \
  -f deploy/agent/backend.Dockerfile -t "$IMAGE" new_backend
```

两个基础镜像参数必须指向已经核对过 image ID 的固定镜像引用，不能使用 `latest`。未配置可信基础镜像缓存时省略这两个参数，继续使用 Dockerfile 中的官方 digest。PyPI 与 npm 参数只接受不含账号、Token 或密码的公开 HTTPS 镜像地址；构建参数会进入镜像历史。构建后记录 image ID、完整 revision label，并与上一生产镜像比较 `pip freeze`；镜像源变化不能作为依赖版本变化的理由。

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

生产构建必须显式使用阿里云 `cloud.env` 中现有的公共 `TURNSTILE_SITE_KEY`，不能沿用本地 `.env.local` 的测试站点 key。发布前比较 key 的 SHA256 与制品 manifest，并检查它确实存在于本次 JS assets 中。Cloudflare `1x000…`、`2x000…`、`3x000…` 测试 key 只用于本地或 Staging。校验失败时保留原生产 `index.html`，先重新构建静态制品，不重建后端或数据库。

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

既有 AF3 兼容入口 `10.9.8.1:18184` 保留历史任务、GPU 结算和 Pi 唤醒。通用计算入口 `10.9.8.1:18186` 由固定版本 Caddy 提供，只允许源地址 `10.9.8.2` 的 claim、heartbeat、result 和产物 GET/PUT。后端仍验证服务密钥和执行租约。该入口没有公网或通用管理 API 路由。

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

UID/GID 必须匹配既有 AF3 spool。CORAL、兼容 AF3、Tools AF3 journal 分别使用 `coral.sqlite3`、`af3.sqlite3`、`af3-product.sqlite3`；任何未确认记录都保留。升级到受保护的 WireGuard MCP 端点前，必须确认旧 journal 已排空；仍有记录则先用旧接收器完成回执，不能删除或修改旧 grant。切换前核对服务端活动任务及旧 journal，停止旧 AF3 接收器，保留模型计算容器，再运行：

```bash
docker compose --env-file /path/to/compose.env \
  -f deploy/agent/compose.a6000-mcp-receiver.yaml up -d --wait
docker logs --tail 30 pskit-mcp-receiver-a6000
```

必须确认 CORAL、兼容 AF3、Tools AF3 三个入口均正常工作，且只有一个接收器领取生产 AF3。两个 AF3 入口共享串行执行通道。旧 [`compose.a6000-receiver.yaml`](compose.a6000-receiver.yaml) 只供经过 journal/owned 核对后的恢复使用，不能与通用接收器同时领任务。

Tools AF3 使用独立的 `af3-mcp` 服务密钥和 `af3-mcp-key` 凭据引用；MCP Bearer 凭据留在阿里云后端与 A6000 接收器私有配置。通用 `compute-*` Job 映射到 UUID spool 目录，公开报告保留原 Job ID，以兼容原生 AF3 计算器。

### CORAL 与 AF3 下载

服务绑定通过 `artifact_tool` 明确选择 `pskit.artifacts.read`。接收器按至多 512 KiB 的块读取文件，校验大小和 SHA256，将完整文件存入既有 PostgreSQL blob 表；文件提交和结果提交都收到回执后才清理 outbox。限制为每文件 64 MiB、每 Job 256 MiB、128 个文件。拥有者只能下载终态报告声明且实际存储匹配的文件。

CORAL 的提供端安装器位于 `new_backend/integrations/coral/install_artifacts.py`。重启提供端时使用同目录的 `launch_artifact_server.py`，显式提供 Python 和 Foldseek 路径；保留 `.pskit-mcp.runtime.env` 中的原有模块路径，并将 workspace 规范为绝对路径。确认没有模型调用执行中，再重启本项目的 MCP PID。不要替换其他人的服务或模型实现。

CORAL 一次生成会从 RCSB 读取 mmCIF。提供端不能直连 RCSB 时，在私有 `.pskit-mcp.runtime.env` 中配置已验收的 `HTTPS_PROXY`；例如当前 4090 使用本机 SOCKS 代理 `socks5h://127.0.0.1:1080`。先用 CORAL 实际 Python 环境下载一个公开 mmCIF 并核对 HTTP 200 和非空内容，再重启 MCP。不要把代理凭据写入仓库；如代理同时承载内网流量，设置 `NO_PROXY` 保留 localhost、WireGuard 和模型内网地址。代理不可达时应让任务明确失败，不得回退到未审核的公共转发服务。

管理员批准历史产物恢复后，在后端容器运行 `python -m scripts.backfill_compute_artifacts --service-id coral-mcp --actor <operator>`。此操作读取已发布历史绑定和终态 Job，校验文件后补存并写审计，不调用推理工具。

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

预期结果：后端就绪；未登录的 `usage` 返回 `401`；公网 `internal` 返回 `404`；登录页返回 `200`；公网管理页面返回 `403`，WireGuard 管理页面返回 `200`。管理 API 公网返回 `403`，WireGuard 未登录返回 `401`。再用授权账号核对登录、项目、对话、SSE、上传、模型选择和 Token 额度。工具发布还需断言已批准的 slug（例如 `coral`）出现在 `/api/v1/tool-products`，打开配置 UI 并提交受限任务；目录 200 或应用代码存在不代表工具已开放。真实 AF3 要另外检查 Job 领取、进度、产物、GPU 结算与 Pi 自动唤醒。2026-10-07 用户确认会员默认每天 60 GPU 设备分钟；真实计算验收仍需使用受控输入及预算上限。不要把只读回调请求当成真实计算成功。

## 7. 停止和恢复

`bash deploy/agent/stack.sh down` 会停止三个生产 Compose 项目，并保留卷。它不负责备份，也不处理 A6000 上未确认的 AF3 回报。停止前先核对运行中的 Run、Job、接收器 journal 和 spool。不要使用 `down -v`。故障恢复后先检查数据库、Pi 会话记录和 AF3 Job 状态，再开放新请求；不要仅根据容器为 `healthy` 就推断用户工作已完成。
