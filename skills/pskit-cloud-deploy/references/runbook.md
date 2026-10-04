# PSKit 日常云端发布操作手册

此手册用于既有环境的代码发布。先读 checkout 的 `deploy/agent/DEPLOYMENT.md` 和 `STAGING.md`；首次安装、数据库迁移、Nginx 路由调整及沙箱启用是不同操作。

## 1. SSH 与主机检查

已知连接是 Windows config 中的 `aliyun`，Linux 用户 `ecs-user`。WSL 示例：

```bash
/mnt/c/Windows/System32/OpenSSH/ssh.exe \
  -F C:/Users/qq289/.ssh/config -o BatchMode=yes -o ConnectTimeout=10 \
  aliyun 'hostname; id; docker ps --format "{{.Names}} {{.Image}} {{.Status}} {{.Ports}}"'
```

用户目录或 alias 变化时先发现真实 config，不把上述路径当固定前提。多行脚本用 `subprocess.run([ssh, ..., "bash -s"], input=script, text=True)`，避免多层 shell 引号。二进制包用 stdin 或原生 scp 的 Windows 路径传输。网络或 Docker 被沙箱阻止时，按运行环境的审批机制执行已授权动作。

检查当前 backend 的 `com.docker.compose.project.config_files`、`project.working_dir`、镜像 ID和 `/data` 挂载。完整 `docker inspect` 仅在程序内解析；输出只允许模式、模型名、网络地址等非秘密字段。确认 `RESEARCH_AGENT_PI_EXECUTION`，未设置时仍以应用默认值为准。

当前常见路径/端口（每次核实）：

| 用途 | 路径或端口 |
| --- | --- |
| 部署代码与生产配置 | `/home/ecs-user/pskit-agent-cloud-20261002` |
| 版本制品 | `/home/ecs-user/pskit-agent-releases/<release>` |
| 生产前端 root | `/var/www/agent.bioailab.net` |
| Staging 前端 root | `/var/www/agent-staging` |
| Staging 私有配置 | `/home/ecs-user/pskit-agent-staging-private` |
| 生产后端 / AF3 代理 | `127.0.0.1:18088` / `127.0.0.1:18185` |
| Staging 后端 / Nginx | `127.0.0.1:18090` / `10.9.8.1:18132` |
| 生产 / Staging LiteLLM | `10.9.8.1:4000` / `10.9.8.1:4002` |

`ecs-user` 有 Docker 权限，静态 root 可能归其所有。先检查可写性，不要默认要求用户输入 sudo 密码。

## 2. 制品身份与构建

从干净或明确记录过的源码构建：

```bash
docker build --label org.opencontainers.image.revision="$COMMIT" \
  -f deploy/agent/backend.Dockerfile -t "$IMAGE" new_backend
(cd new_frontend && VITE_AUTH_MODE=supabase VITE_API_BASE_URL=/api/v1 npm run build)
docker image inspect --format '{{.Id}}' "$IMAGE"
```

运行仓库约定的授权验证。制品记录至少包含 Git commit、image/tag/ID、`prepare_staging._dist_hash(dist)`、前端构建参数和归档 SHA256。源码包用 `git archive`；未提交源码必须单独注明内容哈希。不要用整仓目录递归打包 `.env`、数据、私钥或 node_modules。

阿里云访问 Docker Hub 超时时，在可联网本地用同一 Dockerfile 构建，用 `docker save` 传输，远端 `docker load` 后核对 ID。已缓存生产镜像不等于新的依赖已安装，不能直接 COPY 新代码伪装成完整构建。

## 3. 重复发布 Staging

首次才运行 `prepare_staging.py`。已有环境按下列步骤更新，全部使用现存私有配置：

```bash
export STAGING_CONFIG_DIR=/home/ecs-user/pskit-agent-staging-private
bash deploy/agent/staging.sh down
PYTHONPATH="$PWD" python -m deploy.agent.scripts.pin_staging_release \
  --config-dir "$STAGING_CONFIG_DIR" --backend-image "$IMAGE" \
  --frontend-dist "$RELEASE_DIR/frontend-dist"
bash deploy/agent/staging.sh up
```

`down` 不带 `-v`；pin 命令只改变 `cloud.env` 的镜像和 manifest，备份原 pins，不生成秘密。使用 `STAGING.md` 中固定 release image 的 seed 与 smoke 容器命令；私网 Nginx 已安装时运行不带 `--private-api` 的 smoke。先发布 Staging dist，再验收它返回的 index/assets 与制品一致。

默认 Staging 模型是 stub，不能作为真实供应商验收证据。需要真实联调时使用明确的测试模型、受限虚拟 key 和小额预算，不把生产 master key 放进 Staging 配置。保留独立 auth/DB/用户，AF3 始终 mock；联调结束移除临时授权并恢复 gateway。

模型选择/推理改动可使用已维护的 `deploy/agent/tests/smoke_composer_models.py`。先完成不计费的 smoke，再在阿里云运行：

```bash
docker run --rm --network host --read-only --cap-drop ALL \
  --security-opt no-new-privileges --user "$(id -u):$(id -g)" \
  -e PYTHONPATH=/release -v "$PWD:/release:ro" \
  -v "$STAGING_CONFIG_DIR:/staging:ro" \
  -v "$PWD/infra/litellm/.env.shared:/production-litellm.env:ro" \
  --entrypoint python "$IMAGE" \
  /release/deploy/agent/tests/smoke_composer_models.py \
  --config-dir /staging --production-env-file /production-litellm.env \
  --model '<已配置且支持推理的真实别名>' --model '<另一个已配置的真实别名>'
```

这个独立工具在内存读取生产网关管理员凭据，仅签发测试虚拟 key（两模型白名单、20 分钟、$0.25、3 RPM）。两个临时 Staging 路由只持有该虚拟 key，不修改 generated backend env 或 Compose。它等待模型目录缓存刷新，用合成账号发送 `model` 和 `reasoning_effort=medium`，要求真实 Run 完成并有 `message.delta`；`finally` 删除两个路由及虚拟 key。失败时核对清理状态，不复制生产 master key到 Staging 文件。该调用会产生明确的生产网关测试计费记录，单独记录，不能混入要求生产快照不变的普通 smoke。

检查烟测脚本针对当前单 PostgreSQL，而不是已经移除的独立 LiteLLM 数据库。生产快照不同可能由真实用户活动引起，应调查差异，不能直接忽略。

## 4. 生产定向更新

先读取现用 `cloud.env` 和 `.env.stack` 的非秘密发布参数，比较 schema 版本和依赖。回滚制品保留在权限 0700 的服务器目录。查询当前 PostgreSQL 活动状态：

```sql
BEGIN READ ONLY;
SELECT status, count(*) FROM pskit.agent_runs GROUP BY status;
SELECT status, count(*) FROM pskit.agent_jobs GROUP BY status;
COMMIT;
```

当前模式为空闲本地 Pi 且 schema 未变时，短暂停止 backend 阻止新请求，再确认无未结束工作后切换镜像。数据库/AF3 工作存在时先等待安全点，不删除任务、ACK spool 或 receiver 容器。结构变更需要数据库备份及兼容性验证；普通兼容代码发布不迁移测试数据。

先原子更新现用 `cloud.env` 的唯一 `AGENT_BACKEND_IMAGE` 为已验收的 `$IMAGE`，保留其他字段和 0600 权限。若 `.env.stack` 存在 `STACK_BACKEND_IMAGE`，同时更新它；只更新 `.env.stack` 不会改变下方直接 Compose 命令的镜像。

Compose 必须使用运行容器标签中相同的 `--env-file`、`-p`、`-f` 顺序及卷/网络。程序内解析 resolved config，确认只改变预期镜像后：

```bash
docker compose --env-file deploy/agent/cloud.env \
  -f deploy/agent/compose.yaml -f deploy/agent/compose.cloud.yaml \
  -f deploy/agent/compose.postgres.yaml -p pskit-agent-cloud \
  up -d --no-deps --wait backend
```

需要更新 `.env.stack` 的 `STACK_BACKEND_IMAGE` 时只修改该字段，保持后续 `stack.sh` 指向本次版本。更新回调代理需明确代理代码变更；普通后台 image 更新无需重建 Supabase、Postgres、LiteLLM 或 A6000。

沙箱模式：先验证 Staging 支持对应 overlay，再同步 backend/manager/用户容器的镜像。既有容器可能因 image mismatch 被拒绝。空闲后重建对应 namespace 容器，保留工作卷和 transcript，禁止默认启用或切换模式。

## 5. 静态发布、验收与回滚

已有可写普通 root：保存完整旧目录；复制新 assets，保留旧 assets；其他公开文件随后复制；把新 index 写入同目录临时文件，设 0644 后 `os.replace`。目录 0755、文件 0644，Nginx 才能遍历/读取。新 hash assets 同名时必须内容相同。已有 release symlink 布局则原子换链接。配置不变无需 reload。

检查：

```bash
curl --noproxy '*' --fail http://127.0.0.1:18088/health/ready
curl --fail -I https://agent.bioailab.net/login
curl -sS -o /dev/null -w '%{http_code}\n' https://agent.bioailab.net/api/v1/usage
curl -sS -o /dev/null -w '%{http_code}\n' https://agent.bioailab.net/internal/
```

预期 ready、200、401、404。还需比对 index 和每个新 asset 的内容哈希，确认运行 image ID；经授权测试账号检查模型、推理等级实际传递、流式 Stop、自动滚动及历史会话。部署成功不能仅凭 HTTP 200 或 mock 输出判断。

失败时停止新增工作，恢复旧发布参数、镜像和 index，保留已写入数据。只在 schema/transcript 兼容时回退程序；有新写入时不恢复旧数据库覆盖它们。记录前后版本、验证结果、根本原因和下一步，不声称尚未验证的功能通过。
