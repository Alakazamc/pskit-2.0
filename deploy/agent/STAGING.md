# 新版 PSKit Staging 使用手册

Staging 是阿里云上按需启动的预发布环境。它有单独的 Supabase PostgreSQL 17、LiteLLM 和 Agent Compose 项目，数据、卷、密钥与生产隔离。前端由宿主机 Nginx 在 WireGuard `10.9.8.1:18132` 提供静态 `dist`；不需要前端容器或公网 DNS。AF3 始终是 mock，模型别名 `claude-opus-4-8` 只指向内部替身，不使用真实 GPU 或模型密钥。

## 首次准备

1. 在同一次发布构建中生成固定后端镜像和 `new_frontend/dist`。记录 `docker image inspect --format '{{.Id}}' <镜像>`。当前生成器会把镜像 ID 和前端 `dist` 内容哈希锁定到 Staging manifest；正式发布必须使用相同制品。
2. 在阿里云选择仓库外的私有目录，例如 `/home/ecs-user/pskit-agent-staging-private`。运行：

   ```bash
   python deploy/agent/scripts/prepare_staging.py /home/ecs-user/pskit-agent-staging-private \
     --backend-image pskit-agent-backend:<固定发布标签> \
     --frontend-dist /home/ecs-user/pskit-agent-cloud-20261002/frontend-dist
   ```

   生成器拒绝覆盖现有目录，自动设置目录 0700、文件 0600；密钥只保留在这台主机上。不要把目录或任何 `.env` 加入 Git、备份到公共位置或复制生产 `.env`。
3. 确认 `10.9.8.1` WireGuard 地址、端口 `18131`、`18090`、`4002`、`18132` 空闲，阿里云可用内存至少 4 GiB、可用磁盘至少 10 GiB。正式生产不需要停止。

## 启动和验收

```bash
export STAGING_CONFIG_DIR=/home/ecs-user/pskit-agent-staging-private
bash deploy/agent/staging.sh up
docker run --rm --network host --read-only --cap-drop ALL \
  --security-opt no-new-privileges --user "$(id -u):$(id -g)" \
  -e PYTHONPATH=/release \
  -v "$PWD:/release:ro" -v "$STAGING_CONFIG_DIR:/staging:ro" \
  --entrypoint python pskit-agent-backend:<固定发布标签> \
  /release/deploy/agent/scripts/seed_staging.py --config-dir /staging
docker run --rm --network host --read-only --cap-drop ALL \
  --security-opt no-new-privileges --user "$(id -u):$(id -g)" \
  --group-add "$(stat -c %g /var/run/docker.sock)" \
  -e PYTHONPATH=/release \
  -v "$PWD:/release:ro" -v "$STAGING_CONFIG_DIR:/staging:ro" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /usr/bin/docker:/usr/bin/docker:ro \
  --entrypoint python pskit-agent-backend:<固定发布标签> \
  /release/deploy/agent/tests/smoke_staging.py --config-dir /staging --private-api
```

`up` 先做静态隔离预检，启动 Staging Supabase 并创建单独 `litellm` 逻辑库和 `pskit` schema，再启动 LiteLLM/模型替身、签发虚拟 key，最后启动 Agent。失败时保留 Staging 卷和日志；不会调用生产 `stack.sh`。首轮 seed 通过 Supabase 管理接口创建已确认邮箱的 `staging-user@example.invalid`，再次运行只复用已有项目、会话和文件。测试密码在 `seed.env`，不要贴到聊天或终端日志。

私网 API 验收通过后，在阿里云 root 会话运行：

```bash
STAGING_CONFIG_DIR=/home/ecs-user/pskit-agent-staging-private \
  bash /home/ecs-user/pskit-agent-cloud-20261002/deploy/agent/scripts/install_host_nginx_staging.sh
```

此脚本只安装 `/etc/nginx/conf.d/agent-staging-private.conf` 与 `/var/www/agent-staging`，校验 manifest 中的 `dist` 哈希和 Nginx 语法。已有 Staging 入口时，更新同一入口需显式加 `--replace-staging`；验证或 reload 失败会恢复之前的 Staging 配置。生产 `agent.bioailab.net.conf` 不会被改写。

然后从可访问 WireGuard 的浏览器打开 `http://10.9.8.1:18132/login`，并重复上面的 smoke 容器命令，删去末尾的 `--private-api`，以验证私网 Nginx 入口。

验收包括登录、文件上传、Agent SSE、Token/GPU 额度及用量记录、GPU 超额拒绝、AF3 `simulation=true` 和私有路径拒绝。脚本在前后只读采集生产 `auth.users`、`pskit` 表、候选单库及当前运行中的 LiteLLM 表计数，并校验 LiteLLM 计费相关表内容摘要；任何变化都报错。它不访问 `10.9.8.2`，不提交真实 AF3 任务。阿里云宿主机不要求安装 `httpx`；上述验收命令使用固定后端镜像，Docker socket 只供读取生产容器计数和摘要，运行结束即卸载。

## 日常发布与停止

一次构建的固定后端镜像 ID 和前端 `dist` 先在 Staging 验收，再把**同一制品**发布到 Production。数据库只做兼容的**结构迁移**；若新旧程序不能同时读写该结构，先采用 expand/contract。旧 SQLite→PostgreSQL 历史数据迁移仅在首次正式切换时按既有手册执行一次，日常发布不复制测试数据或生产用户数据。

```bash
bash deploy/agent/staging.sh status
bash deploy/agent/staging.sh logs agent
bash deploy/agent/staging.sh down
```

`down` 仅停止三个 Staging 项目，不带 `-v`，保留账号和测试样本。遇到故障先读 Staging 日志，停止 Staging，不清空卷也不切生产流量。重置测试数据应单独规划并逐一核对 Staging 项目/卷名；不要把当前的迁移候选 LiteLLM 4001 当成 Staging。
