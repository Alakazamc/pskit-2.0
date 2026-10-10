# 既有环境的应用发布

日期：2026-10-10。适用范围：已完成安装的新版生产环境。主机、账号、数据卷和
部署职责见 [部署手册](../../deploy/agent/DEPLOYMENT.md)，Staging 验收见
[STAGING.md](../../deploy/agent/STAGING.md)。发布与回退的制品、静态文件和操作证据
按 [发布操作手册](../../skills/pskit-cloud-deploy/references/runbook.md)维护。

根 `deploy_to_cloud.sh` 已退役：所有参数（包括 help、rollback）只打印说明并返回
`2`，不执行发布。`stack.sh up/down` 是全栈初始化或维护操作；日常兼容代码更新
使用下方 backend 定向命令。数据库迁移单独备份、审查和执行，随后以同一制品做
Staging 验收；应用发布步骤不执行 provisioning 或迁移。

## 发布前配置门禁

先选定源码和固定制品，完成授权的本地回归及隔离 Staging 验收，保存旧制品和
schema/transcript 兼容性证据。以下纯配置检查在前端生产构建和任何生产修改之前
运行；文件路径必须与实际 Compose 使用的私有文件一致：

```bash
python3 deploy/agent/scripts/production_config.py \
  --backend-env-file deploy/agent/cloud.backend.env \
  --cloud-env-file deploy/agent/cloud.env
```

校验要求 Python/PostgreSQL 认证限流为 `enforce`、独立且至少 32 字符的限流/CSRF
secret、仅可信本地 Nginx 代理、启用 captcha，以及匹配生产域名的真实 Turnstile
配置。缺值、占位值和 Cloudflare 的全部公开测试 key 均非零拒绝；校验器只读取
字面量 dotenv 赋值，不执行文件内容。支持字面量引号和 Compose 的空白后行内
注释；认证字段拒绝变量展开、反斜杠转义和命令替换，避免校验值与容器有效值
不同。注释不计入密钥长度或独立性判断。公开测试值来源为
[Cloudflare 官方测试文档](https://developers.cloudflare.com/turnstile/troubleshooting/testing/)。

完整栈的本地配置检查入口为 `bash deploy/agent/stack.sh check-config`，沿用其
私有文件和权限检查，完成前不调用 Docker。它会读取现有 operator-owned
`.env.stack` 的 shell 配置；只检查认证配置时使用上方不 source 文件的 Python
入口。校验通过只代表文件符合门禁。

`TURNSTILE_SITE_KEY` 标记不证明已发布 dist 内编译了相同 key；保留显式构建参数、
制品身份与发布后的字节核对证据，再确认前端和生产 captcha 配置一致。

生产实际 Nginx、运行容器的 `enforce`、PostgreSQL 原子桶、真实 captcha 与限流
429/Retry-After 均为 **needs_live_validation**，必须在有授权的受控 canary 中
核对。Nginx 模板保留 `limit_req_dry_run on` 供观测；它不代表该层已执行硬限流。
检查 `nginx -T` 的有效路由、代理覆写 `X-PSKit-Client-IP` 与实际容器配置后才记录
该层的有效状态。模板和本地替身测试不能充当生产防护验收。

## 只更新 backend

执行主机：阿里云部署 checkout。条件：配置门禁和 Staging 已通过，活动 Run/Job/
workspace attempt 已到安全点，已原子更新实际 `cloud.env` 的固定镜像；如果
`.env.stack` 有 `STACK_BACKEND_IMAGE`，同步该字段。根据运行容器标签核对
`--env-file`、project、全部 `-f` 顺序、挂载和网络。

下面覆盖当前已记录的生产组合。将 `STACK_OPENSANDBOX_ENABLED` 显式设为与
运行标签一致的 `true` 或 `false`；生产已有 OpenSandbox 时必须为 `true`，保留
基础与 production 两个 overlay。自定义路径或额外 overlay 也必须沿用实际组合。

```bash
set -Eeuo pipefail
: "${STACK_OPENSANDBOX_ENABLED:?Set true or false from the deployed Compose file list}"
python3 deploy/agent/scripts/production_config.py \
  --backend-env-file deploy/agent/cloud.backend.env \
  --cloud-env-file deploy/agent/cloud.env
agent_files=(-f deploy/agent/compose.yaml -f deploy/agent/compose.cloud.yaml
             -f deploy/agent/compose.postgres.yaml)
case "$STACK_OPENSANDBOX_ENABLED" in
  true)
    agent_files+=(-f deploy/agent/compose.opensandbox.yaml
                 -f deploy/agent/compose.opensandbox.production.yaml)
    ;;
  false) ;;
  *) printf '%s\n' 'STACK_OPENSANDBOX_ENABLED must be true or false' >&2; exit 2 ;;
esac
agent_compose=(docker compose --env-file deploy/agent/cloud.env
               "${agent_files[@]}" -p pskit-agent-cloud)
"${agent_compose[@]}" config --quiet
"${agent_compose[@]}" up -d --no-deps --wait backend
curl --noproxy '*' --fail --silent --show-error http://127.0.0.1:18088/health/ready
```

只在全部命令成功后继续静态发布及验收。`--no-deps` 保留现有数据库、网关、回调
代理和 OpenSandbox Server；镜像与 compose 组合变化仍需在程序内核对，避免打印
包含凭据的 resolved config。OpenSandbox 制品或能力变更按专门的发布手册执行。

## 验收失败与回退

验收脚本使用 `set -Eeuo pipefail`，ready 或 HTTP 状态断言失败即非零停止：

```bash
set -Eeuo pipefail
curl --noproxy '*' --fail --silent --show-error http://127.0.0.1:18088/health/ready
for probe in 'login 200' 'api/v1/usage 401' 'internal/ 404'; do
  read -r path expected <<< "$probe"
  actual=$(curl --noproxy '*' --silent --show-error --output /dev/null \
    --write-out '%{http_code}' "https://agent.bioailab.net/$path")
  [[ "$actual" == "$expected" ]] || { printf 'Unexpected HTTP status for %s\n' "$path" >&2; exit 1; }
done
```

此外比对运行 image ID、完整 dist 哈希、登录/SSE/上传及本次改动行为。失败时停止
新增工作，以同一完整 overlay 组合恢复已知兼容旧镜像，恢复旧 index 并保留旧
assets、工作卷和已写入的数据。回退程序之前检查 schema/transcript 兼容性；
不以旧数据库备份覆盖新写入。将实际结果及仍待验证的防护写入发布记录。

本地认证回归入口：`deploy/agent/tests/test_auth_security_regression.py` 验证真实
AuthProtection 到 429/Retry-After 的 HTTP 边界；`new_backend/tests/test_csrf.py`、
`test_anonymous_auth.py`、`test_auth_usage.py`、`test_catalog_files.py` 和
`test_file_lifecycle.py` 保留 CSRF、匿名身份和 owner 边界。依赖 `live_database`
的测试只在独立测试 PostgreSQL 上执行；跳过结果不能计为 live 通过。
