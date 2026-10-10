# 新栈 CI 的本地等价入口

`.github/workflows/new-stack.yml` 对 `main`、`codex/new-stack-baseline` 的 push
和全部 PR 执行 Python 3.12/PG17、React、Pi、契约、固定 LiteLLM 协议与正式
backend Dockerfile 门禁。旧 `.github/workflows/ci.yml` 保留用于旧栈维护。
本工作流不部署、不读取 SSH 或供应商凭据，也不连接云端数据库。

首次依赖安装使用生产依赖约束与 npm 锁：

```bash
new_backend/.venv/bin/python -m pip install -c new_backend/requirements.lock -e 'new_backend[dev]'
npm ci --prefix new_frontend
npm ci --prefix new_backend/pi
```

准备独立的 loopback PostgreSQL 17，在进程环境中设置 `TEST_POSTGRES_DSN`，
避免在命令参数中传入 DSN。运行器要求主版本 17，并拒绝非 loopback 地址：

```bash
new_backend/.venv/bin/python scripts/ci_new_stack.py backend --report-dir /tmp/pskit-backend-ci-results
new_backend/.venv/bin/python scripts/ci_new_stack.py frontend
new_backend/.venv/bin/python scripts/ci_new_stack.py pi
new_backend/.venv/bin/python scripts/check_new_stack_contracts.py
new_backend/.venv/bin/python scripts/ci_render_compose.py
new_backend/.venv/bin/python scripts/ci_new_stack.py deployment
```

运行器只复制 Git 已跟踪或未忽略的源码路径，过滤私有 env、数据目录与依赖。
它清除继承的应用、数据库、供应商、Compose 和测试配置，只为后端数据库
fixture 传入指定测试 DSN；其他默认运行配置为 mock。前端与 Pi 复用已经
安装的依赖包，临时 node_modules 保留自己的 Vite 缓存目录；测试、build、
Python cache、Pi sessions 写入临时目录。前端 build
是演示配置的构建门禁；正式发布仍需使用经预检的生产公共配置构建制品。

`--report-dir` 必须位于仓库外，保留完整输出和后端 JUnit XML。诊断停滞可用：

```bash
new_backend/.venv/bin/python scripts/ci_new_stack.py backend --per-file \
  --file-timeout 120 --timeout 1800 --report-dir /tmp/pskit-backend-file-results
```

每个文件含 60 秒 faulthandler trace；超过文件或整体时间上限会终止子进程组。
未设置测试 DSN、PG 版本错误、PG 用例 skip 或 PG suite 没实际执行均使门禁失败。
依赖真实 Supabase 的独立账号用例仍需按其专用入口运行，普通 CI 不连接真实账号。

镜像门禁必须构建当前源码，不能拿旧缓存应用镜像冒充本次构建：

```bash
docker build -f deploy/agent/backend.Dockerfile -t pskit-agent-backend:ci new_backend
PSKIT_BACKEND_IMAGE=pskit-agent-backend:ci new_backend/.venv/bin/python -m pytest -q deploy/agent/tests/test_backend_image.py
```

固定 LiteLLM 的完全离线协议命令和退役探针见
[兼容层维护手册](../../infra/litellm/COMPATIBILITY.md)。Compose 渲染只使用临时
合成 env，不启动任何服务；真实 Staging/preflight/smoke 和 manifest 证据必须
另有实际执行结果。本地通过不等同 GitHub 远端通过；push 后才有远端 CI 证据。

部署回归包含 shared-PG 权限、配置校验及当前路由配置检查。旧 Web 容器的
`test_web_routes.py` 和 `test_split_web_routes.py` 使用迁移时期的 Web 镜像、
容器构建及私有网络，不在新栈门禁中运行；其源码保留，需在该历史入口的独立
构建环境中执行。当前生产 React 由宿主 Nginx 提供，正式镜像门禁针对 backend。
对应当前入口的门禁是 `test_aliyun_return_ingress.py`、`test_staging_nginx.py`
和 `test_host_nginx_auth_limits.py`：核对 SPA fallback、API/SSE proxy、上传边界、
Auth callback/verify、internal/admin 拒绝和安装回滚。它们检查实际使用的配置
模板及隔离安装替身，不能代替真实 Nginx 请求验收；后者留给授权的 Staging smoke。
