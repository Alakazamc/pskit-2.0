# PSKit 本地开发与云端背景

更新时间：2026-10-08（Asia/Shanghai）。用户确认的默认流程是：**本地前后端一起运行 → 本地验证 → 云端 Staging → 云端生产**。

本文用于新对话恢复环境和工作背景。连接配置已在本地核对；云端职责来自最新部署及发布记录，本次发布前已只读核对阿里云和 A6000 运行配置；具体制品及验收状态见末尾交接。每次部署仍需核对目标主机实际状态。

## 1. 新对话从哪里开始

在同一个仓库或包含这些文件的 checkout 中工作，根目录 [AGENTS.md](../AGENTS.md) 指向本文。Codex 在启动时发现项目指令；文件变更后的加载行为见 [OpenAI 官方 AGENTS.md 说明](https://learn.chatgpt.com/docs/agent-configuration/agents-md)。文档保存的是环境事实和明确交接，不等于自动恢复全部旧聊天。

读取本文后，在本地仓库运行：

```bash
cd /home/jhli/pskit-2.0
git status --short --branch
git log -3 --oneline
```

检查末尾交接中的未完成项，再按任务读取对应手册。`task.md` 如在本地提供，可定向搜索原始对话中的决策与证据；当前 checkout 未包含该导出文件。过时的操作记录需结合日期判断。

## 2. 当前代码与部署职责

| 位置 | 职责 | 常用入口 |
| --- | --- | --- |
| 本地 WSL | `new_frontend/` React/Vite；`new_backend/` FastAPI/Pi；开发、测试、构建 | 前端 `127.0.0.1:5174`；API `127.0.0.1:18080` |
| 阿里云宿主机 | Nginx、TLS、生产与 Staging 的 React `dist` | 生产 `https://agent.bioailab.net`；Staging `10.9.8.1:18132` |
| 阿里云 Docker | Python/Pi、Supabase、PostgreSQL 17、LiteLLM、计算回调入口 | 生产 API `127.0.0.1:18088`；Staging API `127.0.0.1:18090` |
| A6000 | 一个统一 MCP/AF3 接收器，以及独立 AF3 模型计算容器 | 主动领取任务；WireGuard 地址 `10.9.8.2` |
| 4090 | CORAL 模型 MCP 服务 | 由审核后的服务绑定指定，A6000 接收器调用 |

生产前端由宿主机 Nginx 提供静态文件。Python/Pi 与业务数据在阿里云；Supabase、LiteLLM、Agent 三个 Compose 项目共享一台 PostgreSQL 17，使用不同逻辑库或 schema 与账号。A6000 接收器保留持久 journal/outbox，计算容器读取持久 spool。详见 [部署手册](../deploy/agent/DEPLOYMENT.md)与 [Compose/端口说明](../deploy/agent/COMPOSE_PORTS.md)。

旧 `frontend/`、`backend/` 和 `pskit.bioailab.net` 属于旧系统。新版常规开发使用 `new_*`，操作云端时保持两套系统各自的配置、数据与服务边界。

## 3. 本地前后端一起运行

### 3.1 日常开发：本地 mock，独立于云端

执行主机：本地 WSL。条件：后端 Python 3.12+、前端 Node/npm 可用；确认 `18080`、`5174` 没有被另一个本地服务占用。若 systemd 已管理新版 API，先确定是在使用该服务还是启动独立开发进程。

首次准备依赖，在后端目录使用独立虚拟环境；已有环境可直接激活：

```bash
cd /home/jhli/pskit-2.0/new_backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

终端 A：启动本地 API。显式使用 mock，且不加载现有 live `.env`：

```bash
cd /home/jhli/pskit-2.0/new_backend
source .venv/bin/activate
RESEARCH_AGENT_MODE=mock RESEARCH_AGENT_RUNTIME=mock \
RESEARCH_AGENT_MCP_EXECUTOR=mock RESEARCH_AGENT_AF3_EXECUTOR=mock \
  python -m uvicorn app.main:app --host 127.0.0.1 --port 18080 --reload
```

终端 B：首次运行 `npm ci`，随后启动前端。命令覆盖现有 `.env.local` 的认证模式，并固定代理目标：

```bash
cd /home/jhli/pskit-2.0/new_frontend
npm ci
VITE_AUTH_MODE=demo VITE_API_BASE_URL=/api/v1 \
DEV_API_PROXY_TARGET=http://127.0.0.1:18080 \
  npm run dev -- --host 127.0.0.1 --strictPort
```

预期：打开 `http://127.0.0.1:5174`，使用开发邮箱进入演示环境；浏览器 `/api/v1` 请求由 Vite 转到本地 API。`http://127.0.0.1:18080/health/ready` 应返回就绪。两个进程都在前台运行，停止时分别按 Ctrl+C。mock 数据随进程生命周期变化，模型和 AF3 输出是替身，不代表真实推理。

### 3.2 真实联调：仍在本地运行前后端

需要真实认证、Pi、数据库或工具时，使用独立的开发配置，保持浏览器 → 本地 Vite → 本地 Python 的链路。

- 本地 Supabase 与开发邮件服务的配置入口见 [本地 Supabase 手册](../infra/supabase/PSKIT.md)。
- `RESEARCH_AGENT_MODE=live` 要求设置 `RESEARCH_AGENT_DATABASE_URL`，指向独立开发 PostgreSQL，提前准备当前代码要求的 schema；Web 进程只检查版本。旧说明中的 SQLite 不能替代当前 live 数据库。
- Pi 依赖与模式见 [后端 README](../new_backend/README.md)。真实模型使用独立开发虚拟 key；真实 GPU 联调使用独立任务入口、接收器身份和 journal，明确输入及预算。
- AF3 的 callback 模式需要接收器能访问本地回调服务。本地 SSH 能登录 A6000，并不意味着 A6000 已能访问 WSL 的本地 API；先验证开发回调链路。

确认本地 `.env` 的数据库、认证及外部服务均属于开发环境后，在终端 A 启动：

```bash
cd /home/jhli/pskit-2.0/new_backend
source .venv/bin/activate
python -m uvicorn app.main:app --env-file .env \
  --host 127.0.0.1 --port 18080 --reload
```

前端使用 `VITE_AUTH_MODE=supabase` 和本地代理。认证的前端 Origin、回调 URL 与实际使用的 `http://127.0.0.1:5174` 保持一致。配置位置、当前本地配置缺口见第 7 节。

已有本地 Docker 联调入口见 [部署目录 README](../deploy/agent/README.md)，网页端口是 `18085`，与 Vite 的 `5174` 是不同启动方式。该入口的历史说明涉及 SQLite；使用当前代码时也需核对 live PostgreSQL 配置和迁移，不能仅依据旧验收记录启动。

## 4. 从 WSL 连接阿里云与 A6000

### 4.1 已核对的 Windows SSH 配置

当前 SSH 程序：`/mnt/c/Windows/System32/OpenSSH/ssh.exe`。Windows 配置文件是 `C:/Users/qq289/.ssh/config`，WSL 可读路径为 `/mnt/c/Users/qq289/.ssh/config`。私钥保留在 Windows，由现有配置选择。

| SSH 别名 | 远程用户 | 连接关系 |
| --- | --- | --- |
| `aliyun` | `ecs-user` | 直接连接阿里云 |
| `jhli-a6000-wg` | `jhli` | `ProxyJump aliyun`，目标地址由 Windows config 指定 |
| `jhli-4090-wg` | `jhli` | `ProxyJump aliyun`，仅 CORAL 提供端任务需要 |

执行主机：本地 WSL。只读检查阿里云及 A6000 的主机身份：

```bash
/mnt/c/Windows/System32/OpenSSH/ssh.exe \
  -F C:/Users/qq289/.ssh/config -o BatchMode=yes -o ConnectTimeout=15 \
  aliyun hostname

/mnt/c/Windows/System32/OpenSSH/ssh.exe \
  -F C:/Users/qq289/.ssh/config -o BatchMode=yes -o ConnectTimeout=15 \
  jhli-a6000-wg hostname
```

去掉末尾 `hostname` 可进入交互终端。用户目录或机器变化时，先找到真实 Windows config 并核对别名；Linux `ssh` 不会自动读取这份 Windows 配置。仓库旧 `connect_*.ps1` 使用另一套 `pskit-*` 别名及 `scripts/ssh_config.windows`，当前 checkout 未发现该配置文件，不能直接作为现行连接入口。

### 4.2 SSH 操作链路与服务运行链路

```text
开发者操作：WSL → Windows OpenSSH → 阿里云 → 跳板后的 A6000/4090
生产任务：  A6000 接收器 → WireGuard → 阿里云领取/回报入口
模型执行：  A6000 接收器 → AF3 spool/计算容器，或 → 4090 CORAL MCP
```

这条 SSH 跳板链路使用阿里云已有路由，本地无须为 SSH 另建 WireGuard 或手工添加 WSL 路由。SSH config 的目标地址与服务使用的 `10.9.8.*` 地址分别从各自配置读取。

阿里云 WireGuard 地址为 `10.9.8.1`：AF3 兼容回调 `18184`、通用计算回调 `18186`，均限制 A6000 来源并验证凭据。A6000 已审核的 AF3 MCP 为 `10.9.8.2:18187/mcp`，要求服务端 Bearer 凭据。详细协议和当前启用范围以部署手册为准。

### 4.3 本地浏览器查看云端 Staging

执行主机：本地 WSL，调用 Windows SSH。条件：Windows 本机端口 `18472` 空闲、云端 Staging 已运行：

```bash
/mnt/c/Windows/System32/OpenSSH/ssh.exe \
  -F C:/Users/qq289/.ssh/config -o BatchMode=yes \
  -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -N -L 127.0.0.1:18472:10.9.8.1:18132 aliyun
```

保持该终端运行，在 **Windows 浏览器**访问 `http://localhost:18472`，结束时 Ctrl+C。转发的监听端属于 Windows；WSL、容器及应用内浏览器可能处于其他网络环境，分别验证其可达性。使用 localhost 也避开了现有上传流程在私网 HTTP IP 地址上缺少浏览器安全上下文的问题。

文件传输复用 Windows SSH/SCP。Windows `scp.exe` 的本地路径使用 Windows 可见路径；WSL 制品可通过 SSH stdin 传输。二进制归档在两端核对 SHA256；多行脚本通过 `bash -s`/stdin 传入，避免多层引号。实现细节见 [发布操作手册](../skills/pskit-cloud-deploy/references/runbook.md)。

## 5. 从本地修改到云端发布

执行流程与完成条件：

1. **本地开发。** 修改新版代码，在本地前后端验证目标行为。前端检查入口为 `npm run typecheck`、`npm run lint`、`npm test`、`npm run build`；后端按改动运行相关 pytest。修改 API 契约时按前端 README 导出 OpenAPI 和生成类型。
2. **形成制品。** 选定源码提交；未提交源码先形成明确内容哈希的快照。使用固定 Dockerfile、锁文件与唯一版本标签，记录镜像 ID、完整 dist 哈希及归档 SHA256。生产前端显式设置 Supabase 模式、`/api/v1` 和生产公共 Turnstile site key，校验其与生产配置一致。
3. **Staging 验收。** 传输并核对同一份制品，复用已有 Staging 私有配置；在独立数据库、账号、卷中检查登录、SSE、上传、配额和改动行为。默认模型及 AF3 为替身；需要真实联调时单独设置受限测试凭据与预算。
4. **生产定向更新。** 保留旧制品和回滚入口，核对活动 Run/Job 与 schema 兼容性。前端修改发布静态文件；后端修改定向更新 backend；接收器修改才更新 A6000 对应制品。沿用生产配置、数据卷和网络，不重复执行首次安装或迁移。
5. **验收与交接。** 核对实际镜像/静态哈希、API ready、登录页 200、未登录 usage 401、公网 internal 404，以及本次用户行为。真实工具验证另查执行、产物、结算及回执。写发布记录与回滚方法，再更新第 7 节。

生产前端先发布 assets，最后原子替换 index，保留旧 assets。静态目录可写且 Nginx 配置不变时，不需要 sudo 或 reload。`ecs-user` 的 SSH 密钥登录不等于具备 sudo 权限；确需改主机配置时，先明确 root 操作的具体内容。

详细命令只维护在 [DEPLOYMENT.md](../deploy/agent/DEPLOYMENT.md)、[STAGING.md](../deploy/agent/STAGING.md)与 [发布操作手册](../skills/pskit-cloud-deploy/references/runbook.md)。本文件提供流程与入口；用户本次确认的是文档和默认工作方式，具体生产发布仍以当次任务的授权范围为准。

## 6. 路径、配置与历史边界

以下云端路径来自已有手册，操作前用实际 Compose 标签、挂载和 Nginx root 核对：

| 用途 | 当前已记录位置 |
| --- | --- |
| 本地源码 | `/home/jhli/pskit-2.0` |
| 阿里云部署 checkout | `/home/ecs-user/pskit-agent-cloud-20261002` |
| 阿里云发布制品 | `/home/ecs-user/pskit-agent-releases/<release>` |
| 生产静态目录 | `/var/www/agent.bioailab.net` |
| Staging 静态目录 | `/var/www/agent-staging` |
| Staging 私有配置 | `/home/ecs-user/pskit-agent-staging-private` |
| A6000 接收器目录 | `/data/jhli/pskit-mcp-receiver-20261007`；具体 runtime 子目录按本次制品选择 |

本地配置位置为 `new_backend/.env`、`new_frontend/.env.local`、`infra/supabase/.env`；生产配置位置见部署手册中的 `cloud.env`、`cloud.backend.env`、`cloud.proxy.env`、`.env.stack` 和基础设施私有文件。文档记录文件用途与位置，密钥、DSN 密码和 Cookie 留在私有配置中；查看运行状态时只输出必要的非秘密字段。

识别历史说明时按以下事实判断：

- 2026-10-02 后端曾迁到 A6000，后来迁回阿里云；`A6000_MIGRATION.md`、`OPERATIONS.md` 中的旧运行位置属于历史。
- 当前 live 业务存储使用 PostgreSQL，旧 SQLite 的迁移、备份命令只用于其对应历史数据。
- 2026-10-07 AF3 接收器合并到 A6000 通用 MCP 接收器；发布 skill 中“A6000 只运行 AF3 接收器”的旧描述不能覆盖最新部署手册的统一接收器职责。
- 应用 RAG 的 `knowledge/` 不是开发运维背景的存放位置。

A6000 升级或恢复前核对唯一接收器、活动任务、journal/outbox/spool，保留未确认结果及模型计算容器。日常代码发布保留数据库与工作区；停止 Compose 时保留卷，回滚程序前检查当前 schema 和新写入的兼容性。

## 7. 当前交接与维护

快照日期：2026-10-08 00:54（Asia/Shanghai）。流式性能发布和后续 A6000 收尾均已完成；生产制品见 [流式发布记录](releases/2026-10-07-streaming-quality.md)，接收器、对账、镜像源与 CORAL provider 证据见 [A6000 收尾记录](releases/2026-10-08-a6000-runtime-mirrors.md)。

- **已确认的用户偏好：** 本地前后端一起运行，完成开发与验证后部署云端。
- **当前源码与部署：** 主仓库当前提交为 `e164dab8363915cd2e8daff5bc8fbf6169712af0`；阿里云生产与 Staging backend 仍使用 `a28b9af069cfcccbd42e1d3f009abc9b2c7a1abe`，A6000 接收器使用 `e164dab` runtime。`e164dab` 只增加后续镜像构建入口和部署说明，不要求重建当前阿里云后端。
- **本地验证：** 前端 typecheck、lint、284 项测试和生产构建通过，Molstar 为 18 个延迟 chunk。后端 Ruff 与 7 项对账/CORAL 回归通过；完整 suite 为 577 passed、281 skipped，一个既有 MCP 租约时序用例在整套高负载下失败、隔离连续 5 次通过。不能据此宣称完整 suite 全绿；未运行真实付费模型或 GPU。
- **生产与 Staging：** 两环境前端均为 dist 哈希 `f4eb708783ed2b95f1286fbc2b554ca19d04317bb36ed1b95109e296e01f85ba`，401 个新文件逐一核对。两环境后端均为 `pskit-agent-backend:20261007-streaming-a28b9af`，阿里云配置 ID `sha256:d713d9b57327ac231462c8b229cfaebb35178ba88413145409b8cebc3cc73e5f`。Staging 私有 API和 WireGuard Nginx 两轮 smoke 均通过登录、文件、SSE、配额和模拟 AF3，生产只读快照未变。生产 ready、HTTPS 200、usage 401、internal 404、首屏资源与未完成 Run=0 均已核对；基础设施和 Nginx 未重启。
- **A6000：** 唯一接收器已挂载 `runtime-e164dab`，三个 journal 均无未确认记录；原 AF3 计算容器 ID 和启动时间不变。两条旧 CORAL Job 已基于提供端证据完成管理员审计结算，中央无非终态或待对账 Job。4090 CORAL provider 已固化失败计量和产物读取修复到独立仓库提交 `25afdc0`；四个工具发现通过，修复后没有运行真实模型推理。
- **发布性能结论：** 此次慢点是 WSL/Docker NAT 到 Docker Hub/PyPI；默认 PyPI 约 20 KB/s，清华镜像约 8–12 MB/s。`e164dab` 已给固定 Dockerfile 增加显式 PyPI/npm 镜像与已验证基础镜像参数；镜像源检查构建的 npm 层约 7 秒、pip 层约 20 秒，新旧 `pip freeze` 一致。生产依赖仍由锁文件和固定基础镜像 digest 决定。
- **本地配置缺口：** 现有 live `.env` 未配置独立开发 PostgreSQL；日常开发使用第 3.1 节的独立 mock，真实联调补齐开发数据库后进行。
- **4090 外部数据：** CORAL 一次生成需要读取 RCSB mmCIF。该主机直连 RCSB 会被拒绝，私有 `.pskit-mcp.runtime.env` 使用已验收的本机 SOCKS HTTPS 代理；重启提供端时由 `launch_artifact_server.py` 传入进程。代理配置不进入 Git，升级或重启前先用 CORAL 实际 Python 环境验证公开 mmCIF 下载。
- **下一步：** 真实 CORAL/AF3 结果验收另设明确输入及预算；下一次后端镜像发布可直接使用已记录的可信依赖镜像参数，并继续比较 `pip freeze`、revision label 和最终 image ID。本地开发进程接续时按实际端口重新核对。

每次交接更新此节的日期、分支/提交、未完成项、实际验证结果、生产/Staging 状态及下一步。镜像 ID、制品哈希和回滚证据写入对应发布记录并链接到此处；连接别名、端口或职责变化则同时修改前面的环境章节。
