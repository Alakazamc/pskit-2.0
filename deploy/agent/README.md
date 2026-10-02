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

本地镜像和数据仅服务联调。云端部署须另行配置真实模型网关、SMTP、域名/TLS 和接收器连通性，并先决定是否让 Python/Pi 与 AF3 一起部署到 A6000。
