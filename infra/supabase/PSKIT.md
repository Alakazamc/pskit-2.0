# PSKit 本地 Supabase

本目录是 Supabase 官方自托管 Docker 配置的固定快照：`self-hosted/v0.8.2`，对应上游提交 `564eab8ad7840b13324f68b1bfac074ef8d51c21`。原始配置来自 [supabase/supabase](https://github.com/supabase/supabase/tree/self-hosted/v0.8.2/docker)。`compose.pskit.yaml` 仅添加 PSKit 的本机端口、开发邮件服务和验证码模板；所有镜像都有明确标签，没有 `latest`。升级时先看上游自托管变更说明，不要直接更新单个组件镜像。

## 本机服务

| 服务 | 地址 | 用途 |
| --- | --- | --- |
| Supabase 网关及 Studio | `http://127.0.0.1:18130` | Auth、Studio、REST 等 |
| Mailpit | `http://127.0.0.1:18131` | 本地确认、找回和改邮箱邮件 |
| Postgres 会话池 | `127.0.0.1:15432` | 仅本机管理用途 |
| Postgres 事务池 | `127.0.0.1:16543` | 仅本机管理用途 |

运行中的本地密钥在本目录被 Git 忽略的 `.env`；Python 使用 `new_backend/.env` 中的 publishable key，前端只使用 `new_frontend/.env.local` 中的公开模式配置。不要把 Supabase secret key、service role key 或数据库密码放进前端环境文件。PSKit 目前只把 Supabase 当身份服务；应用数据仍由 Python 管理。当前 mock Agent 模式使用进程内存，切换到 Pi 运行时才使用 SQLite 持久会话。

首次在另一台机器配置时，先从 `.env.example` 创建权限为 `0600` 的 `.env`，运行官方 `utils/generate-keys.sh --update-env` 和 `utils/add-new-auth-keys.sh --update-env`，再设置 `COMPOSE_FILE=docker-compose.yml:compose.pskit.yaml`、本页端口对应的 `SUPABASE_PUBLIC_URL` / `API_EXTERNAL_URL`、前端 `SITE_URL`，以及 `SMTP_HOST=mailpit`、`SMTP_PORT=1025`。当前机器已生成独立密钥。不要把另一套 `szutk-supabase` 的数据库或凭据复制到这里。

```bash
cd infra/supabase
docker compose pull
docker compose up -d --wait
docker compose ps
```

`docker compose down` 会停止服务但保留数据库；不要使用 `down -v` 或 `reset.sh`，除非确定要删除这套实例的数据。前端在 `new_frontend/` 执行 `npm run dev -- --strictPort`；Python 在 `new_backend/` 执行 `python -m uvicorn app.main:app --env-file .env --host 127.0.0.1 --port 18080`。当前 `RESEARCH_AGENT_RUNTIME=mock`，因此切换的是**真实 Supabase 认证**，Agent 仍是 mock，且本地未连接模型网关、远程 MCP 或 AF3 计算服务。

邮箱注册、验证码确认、密码登录及 Python JWT 校验已通过本地接口联调。Mailpit 只供开发使用，不会把邮件送到真实邮箱。Google 登录需要另行设置 Google OAuth 凭据及回调地址；游客登录在 Python live 模式下仍关闭，因为上线前必须配置 CAPTCHA/Turnstile 和共享限流密钥。生产部署也需要 HTTPS、真实 SMTP、备份与可信反向代理。

## 阿里云独立实例

`compose.cloud.yaml` 在固定版本配置上建立 `pskit-agent-supabase`，只向宿主机回环地址发布网关，Postgres 与文件存储使用独立 Docker 命名卷。应用步骤见 [`../../deploy/agent/OPERATIONS.md`](../../deploy/agent/OPERATIONS.md)。必须生成全新密钥，不复制本机 `.env`、账号或数据库。未配置真实 SMTP 时保持 `CLOUD_DISABLE_SIGNUP=true`，仅供私网验证。
