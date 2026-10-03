# 新版 Agent 本地 Docker 联调

这套 Compose 在本机启动 React/Nginx、Python/Pi 和独立的 AF3 回调代理。它复用本机已运行的 Supabase Docker 网络，使用独立的 `pskit-agent-local_agent_data` 卷；模型由 Compose 内的替身响应，AF3 由联调脚本模拟领取和完成。现有 WSL 后端及 A6000 接收器不需要停机。

## 准备

先按 [`../../infra/supabase/PSKIT.md`](../../infra/supabase/PSKIT.md) 启动本机 Supabase；其 Docker 网络默认为 `pskit-supabase_default`。将 [`local.env.example`](local.env.example) 复制为被 Git 忽略的 `local.backend.env`，填入该 Supabase 实例的公开 publishable key，并生成一枚新的、至少 32 字符的测试回调密钥。同一密钥还须写入单独的 `local.proxy.env`，格式为 `RESEARCH_AGENT_COMPUTE_CALLBACK_KEY=...`。这两份文件须设为 `0600`，不要复用正在服务 A6000 的回调密钥。Compose 的模型替身密钥固定为 `local-test-only`，不调用付费模型。

```bash
cd /home/jhli/pskit-2.0
umask 077
cp deploy/agent/local.env.example deploy/agent/local.backend.env
# 编辑 local.backend.env，并创建仅含回调密钥的 local.proxy.env
chmod 600 deploy/agent/local.backend.env deploy/agent/local.proxy.env
docker compose -f deploy/agent/compose.yaml -f deploy/agent/compose.local.yaml config --quiet
docker compose -f deploy/agent/compose.yaml -f deploy/agent/compose.local.yaml up -d --build
```

网页地址是 <http://127.0.0.1:18085>。仅本机可访问的后端诊断端口为 `18088`，AF3 回调代理端口为 `18185`。`18080` 和 `18084` 属于现有 WSL 服务，本套 Compose 不占用。停止本地联调时用同一组 `-f` 参数执行 `down`；保留卷可保留账号关联的项目、Run、Pi 会话和 AF3 记录。

## 验证

```bash
python deploy/agent/tests/smoke_local.py
```

脚本使用本机 Supabase/Mailpit 创建或复用测试身份，走 Python 登录、项目、会话、真实 Pi CLI、SSE、文件上传、AF3 挂起、容器重启、模拟 Worker 完成、自动唤醒、配额和产物检查。首次生成的测试凭据保存在被 Git 忽略的 `deploy/agent/local.smoke.credentials`，权限 `0600`；也可以用 `AGENT_SMOKE_EMAIL` 和 `AGENT_SMOKE_PASSWORD` 指向自己的测试账号。脚本只打印状态和事件数。这里的 AF3 结果带 `simulation=true`，不代表 GPU 推理已在 Docker 栈运行。

2026-10-02 本机验证：`smoke_local.py` 通过，普通对话记录 4 个事件，AF3 Run 记录 13 个事件；后端完整 pytest 为 385 passed，前端 135 passed，类型检查、lint 和构建通过。基础镜像固定为 Python `3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c`、Node `22.21.1-bookworm-slim@sha256:25b3eb23a00590b7499f2a2ce939322727fcce1b15fdd69754fcd09536a3ae2c`、Nginx `1.28.0-alpine@sha256:30f1c0d78e0ad60901648be663a710bdadf19e4c10ac6782c235200619158284`；Pi 使用 `new_backend/pi/package-lock.json` 锁定的 `0.87.1`。Python 依赖目前仍按 `pyproject.toml` 的版本范围安装，尚未使用完整锁文件。

本地镜像和数据仅服务联调。云端单 PostgreSQL 切换参见下节；A6000 只保留 AF3 接收器与计算。

## 阿里云单 PostgreSQL 启动入口

[`stack.sh`](stack.sh) 管理现有三个 Compose 项目：Supabase、只含 gateway 的 LiteLLM、Python/Pi 与 AF3 回调代理。React 的 `dist` 由宿主机 Nginx 提供；`web` 容器不参加默认启动。LiteLLM 与 Python 使用 Supabase PostgreSQL 17 的不同逻辑数据库和受限账号。

运行前在阿里云准备以下 **0600** 私有文件。`infra/litellm/.env.shared` 是新库的凭据，不能直接复制旧 `infra/litellm/.env`；`deploy/agent/cloud.backend.env` 的模型 key 必须是候选网关新生成的 key。

| 文件 | 用途 |
| --- | --- |
| `infra/supabase/.env` | 现有 Supabase 实例配置 |
| `infra/litellm/.env.shared` | 新库 LiteLLM 密码、master/salt、`LITELLM_BIND_IP=10.9.8.1`、`LITELLM_PUBLIC_PORT=4000` |
| `deploy/agent/cloud.env` | 固定版后端镜像、回调代理配置、站点地址，以及一个全新的 `AGENT_PG_DATA_VOLUME` |
| `deploy/agent/cloud.backend.env` | PostgreSQL `pskit_app` DSN、新虚拟 key、Supabase 公钥、回调密钥 |
| `deploy/agent/cloud.proxy.env` | 与后端一致的 AF3 回调密钥 |
| `deploy/agent/.env.stack-admin` | `SHARED_POSTGRES_ADMIN_DSN`、`LITELLM_DB_PASSWORD`、`PSKIT_DB_PASSWORD`；仅供一次性创建角色与迁移 |

管理 DSN 指向 Docker 网络内的 `db:5432/postgres`。若文件不在上述路径，可用同样权限的 `deploy/agent/.env.stack` 设置 `STACK_SUPABASE_ENV_FILE`、`STACK_LITELLM_ENV_FILE`、`STACK_BACKEND_ENV_FILE`、`STACK_CLOUD_ENV_FILE`、`STACK_PROXY_ENV_FILE` 和 `STACK_ADMIN_ENV_FILE`。该文件由 bash 加载，只允许受信任的运维人员编辑。所有私有文件与模型密钥均不提交到 Git。

在完成 [`SINGLE_POSTGRES_CUTOVER.md`](SINGLE_POSTGRES_CUTOVER.md) 的备份、快照导入与候选模型验收后，再运行：

```bash
deploy/agent/stack.sh up
deploy/agent/stack.sh status
deploy/agent/stack.sh logs agent
```

`up` 依次等待 Supabase、创建/检查两个应用账号、等待 LiteLLM、执行一次性 `pskit` schema 迁移，最后启动并检查后端与回调代理。缺凭据、旧 LiteLLM PostgreSQL 16 仍运行或健康检查失败时，后端不会启动。`down` 按反序停止三个项目，保留所有卷；不应作为切换过程中冻结写入的替代命令。
