# 新版 PSKit 后端迁回阿里云

本手册只处理 `agent.bioailab.net` 的新版服务。阿里云继续由宿主机 Nginx 提供 React `dist`，并运行 Supabase、LiteLLM；A6000 保留 AF3 接收器和计算容器。旧 `pskit.bioailab.net` 及其容器不动。设计见 [规格](../../docs/superpowers/specs/2026-10-03-agent-aliyun-backend-return-design.md)。

| 项目 | 当前 | 目标 |
| --- | --- | --- |
| 新版 Python/Pi | A6000 `pskit-agent-a6000-backend-1` | 阿里云 `pskit-agent-cloud-backend-1` |
| Agent 数据 | A6000 卷 `pskit-agent-a6000_agent_data` | **新**阿里云卷 `pskit-agent-cloud-return-20261003_agent_data` |
| 公网 `/api/v1/` | `10.9.8.2:18088` | `127.0.0.1:18088` |
| AF3 接收器目标 | `127.0.0.1:18185` | `http://10.9.8.1:18184` |
| AF3 回调代理 | A6000 `127.0.0.1:18185` | 阿里云 `127.0.0.1:18185` |

正式切换前**重新检查任务状态**。2026-10-03 的一次检查显示 A6000 有 9 个已完成、1 个失败的 Run，AF3 job 与 receiver journal 均为 0；这些数字不是后续操作的许可。整个切换只能有一个新版后端写 Agent 数据。不要执行 `docker compose down -v`，也不要从阿里云旧卷 `pskit-agent-cloud_agent_data` 启动旧后端。

## 1. 准备，不接管

两机部署目录分别是：

- 阿里云：`/home/ecs-user/pskit-agent-cloud-20261002/deploy/agent`
- A6000：`/data/jhli/pskit-agent-a6000-20261002/deploy/agent`

先把本仓库已审阅的 `compose.cloud-return.yaml`、`host-nginx-agent-aliyun.conf`、两个新 Nginx 脚本和 `compose.a6000-receiver.yaml` 分别同步到对应部署目录；不复制 Git 中的 `.env.example` 覆盖服务器的真实 `.env`。阿里云当前只装有旧后端镜像，须把 **A6000 当前运行的固定镜像** `pskit-agent-backend:litellm-20261003` 离线传入阿里云。镜像归档、SHA-256 文件和 Agent 快照都放权限 `0700` 的专用 `transfer/` 或 `backups/` 目录；跨主机可用已配置的 SSH 别名和 `scp -3`。验证 `sha256sum -c` 后才 `docker load`；核对加载后的镜像 ID 与 A6000 `docker image inspect ... --format '{{.Id}}'` 一致。不要用 `latest` 或重新构建未确认的代码替代正在运行的镜像。

阿里云 `.env`（权限 `0600`）须将 `AGENT_BACKEND_IMAGE` 固定为上述标签，`AGENT_BACKEND_ENV_FILE`、`AGENT_AF3_PROXY_KEY_FILE` 指向分别受限的云端文件。`cloud.backend.env` 必须配置真实 `MODEL_GATEWAY_KIND=litellm`、`MODEL_GATEWAY_BASE_URL=http://10.9.8.1:4000/v1`、`MODEL_GATEWAY_MODEL=claude-opus-4-8`，API key 从阿里云现有 `infra/litellm/.pskit-virtual-key` 私密文件读取，不在命令行或日志中打印。Supabase publishable key 沿用云端已有配置；AF3 回调密钥与 A6000 receiver 当前密钥保持一致。`cloud.proxy.env` 只含回调密钥。检查文件权限为 `0600`，后端 Compose 渲染及网络连接可用。当前普通用户 GPU 每日额度仍为 0，不在迁移中放宽。

阿里云部署目录先检查配置，但**不要**运行 `up backend`：

```bash
cd /home/ecs-user/pskit-agent-cloud-20261002/deploy/agent
docker compose --env-file .env -f compose.yaml -f compose.cloud.yaml -f compose.cloud-return.yaml config --quiet
docker image inspect pskit-agent-backend:litellm-20261003 --format '{{.Id}}'
docker volume inspect pskit-agent-cloud_agent_data --format '{{.Name}}'
docker ps --format '{{.Names}} {{.Status}}'
```

确认 Supabase 与 LiteLLM 健康，云端 `10.9.8.1:4000` 与 A6000 `10.9.8.2` WireGuard 可达，磁盘足以同时保留新卷、旧卷和两个快照。阿里云 `ecs-user` 无 sudo；本手册的 Nginx 命令最终由用户在云助手 root 会话运行准备好的脚本。

## 2. 冻结门槛

在 A6000 后端容器用只读 SQLite 查询 `agent_runs` 和 `agent_jobs` 状态；只允许 Run 为 `completed`、`failed`、`cancelled`，job 为 `completed`、`failed`、`cancelled`。`queued`、`running`、`waiting`、`resume_queued` 等任何非终态都须先等待或对账。A6000 receiver 的 `/var/lib/af3-receiver/journal.sqlite3` 中 `jobs` 必须为 0；检查 spool 中是否有未 ACK 的结果、计算容器是否还在运行任务。任一结果不清楚就中止切换，不删除 journal/spool。重新确认 AF3 回调 proxy 的 owned-jobs 为 0。

确认无任务后，停止顺序是 **A6000 receiver → A6000 新版回调代理和后端**。计算容器 `pskit-af3-compute-real-test-20261002`、旧 `pskit2-*` 和云端 Supabase/LiteLLM 不停止。此时公网 API 会短暂不可用，React 静态页仍可显示。停止后再查询容器状态；不要在写入者仍运行时复制 SQLite 或 Pi 文件。

```bash
cd /data/jhli/pskit-agent-a6000-20261002/deploy/agent
docker compose -f compose.a6000-receiver.yaml --profile cutover stop af3-receiver
docker compose -f compose.a6000.yaml stop af3-callback-proxy backend
docker ps --format '{{.Names}} {{.Status}}'
```

## 3. 一致性快照和恢复

先用仓库既有 `scripts/agent_data_snapshot.py` 对阿里云旧卷做**只读备份**，明确标为 `pre-return-cloud`；再对已停机的 A6000 当前卷做最终快照，明确标为 `return-source`。脚本将已提交 WAL 合入完整 SQLite，复制 `pi-sessions/`，生成逐文件 SHA-256 清单并验证数据库完整性。源卷和旧快照一直保留。

在 A6000 部署目录执行最终源快照：

```bash
umask 077
mkdir -p backups/return-20261003
docker run --rm --network none --user 0:0 \
  -v pskit-agent-a6000_agent_data:/source:ro \
  -v "$PWD/backups/return-20261003":/backup \
  -v "$PWD/scripts/agent_data_snapshot.py":/script.py:ro \
  pskit-agent-backend:litellm-20261003 \
  sh -c 'python /script.py snapshot /source /backup/agent-snapshot --source-stopped && chown -R 1006:1006 /backup/agent-snapshot'
tar -C backups/return-20261003 -czf backups/return-20261003/agent-snapshot.tar.gz agent-snapshot
(cd backups/return-20261003 && sha256sum agent-snapshot.tar.gz > agent-snapshot.tar.gz.sha256)
```

通过 SSH 把 `agent-snapshot.tar.gz` 和 `.sha256` 送到阿里云部署目录下权限 `0700` 的 `transfer/return-20261003/`。在阿里云验证并恢复至**全新空卷**，不可恢复到 `pskit-agent-cloud_agent_data`：

```bash
cd /home/ecs-user/pskit-agent-cloud-20261002/deploy/agent
umask 077
(cd transfer/return-20261003 && sha256sum -c agent-snapshot.tar.gz.sha256)
tar -C transfer/return-20261003 -xzf transfer/return-20261003/agent-snapshot.tar.gz
docker volume create pskit-agent-cloud-return-20261003_agent_data
docker run --rm --network none --user 0:0 \
  -v pskit-agent-cloud-return-20261003_agent_data:/target \
  -v "$PWD/transfer/return-20261003/agent-snapshot":/snapshot:ro \
  -v "$PWD/scripts/agent_data_snapshot.py":/script.py:ro \
  pskit-agent-backend:litellm-20261003 \
  sh -c 'python /script.py restore /snapshot /target && chown -R 10001:10001 /target'
```

`restore` 会验证清单、各文件摘要及 SQLite 完整性，并拒绝非空目标。恢复失败时保留两端源卷，不启动云端后端；查明原因后使用另一个全新空卷重做，不能删除已写入的卷掩盖问题。记录归档 SHA-256 和两个镜像 ID，不记录会话内容。

## 4. 云端私网验收与接收器重指向

阿里云只启动 `backend af3-callback-proxy`，不要启动 `web` 或模型 mock：

```bash
cd /home/ecs-user/pskit-agent-cloud-20261002/deploy/agent
docker compose --env-file .env -f compose.yaml -f compose.cloud.yaml -f compose.cloud-return.yaml up -d backend af3-callback-proxy
docker compose --env-file .env -f compose.yaml -f compose.cloud.yaml -f compose.cloud-return.yaml ps
curl --noproxy '*' -fsS http://127.0.0.1:18088/health/ready
curl --noproxy '*' -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18185/internal/compute/af3/jobs/owned
```

后端 readiness 应为 200；无密钥 AF3 请求应为 404。使用已有测试账号私网核对 Supabase 登录、项目、会话、Pi 历史、上传文件、Token/GPU 余额以及 LiteLLM `claude-opus-4-8` 的真实调用与用户预算归属。若这些检查失败，不切公网。

云端回调代理就绪后，用户在**阿里云 root 会话**运行：

```bash
bash /home/ecs-user/pskit-agent-cloud-20261002/deploy/agent/scripts/enable_private_af3_ingress.sh
```

脚本只将原 `agent-af3-private.conf.disabled-20261002` 恢复为私网监听 `10.9.8.1:18184`，只接受来源 `10.9.8.2`，失败时自动禁用。随后从 A6000 无密钥访问应为 404，有效密钥和 worker ID 读取 owned-jobs 应成功。不要在聊天、命令行或日志中输出密钥。

在 A6000 部署目录用 `AGENT_AF3_API_URL=http://10.9.8.1:18184` 渲染并**重建同一个** receiver 服务；保持原 worker ID、镜像、journal/spool 挂载和 compute 容器。确认容器只有一个、重启次数无异常。执行一个受控真实 AF3 小任务，核对 claim、进度、`simulation=false`、产物下载、GPU 扣费、结果 ACK、journal 清理和 Pi 自动唤醒；以任务 ID、后端记录及实际产物为证据，不能用 mock 结果替代。

```bash
cd /data/jhli/pskit-agent-a6000-20261002/deploy/agent
AGENT_AF3_API_URL=http://10.9.8.1:18184 docker compose -f compose.a6000-receiver.yaml --profile cutover config --quiet
AGENT_AF3_API_URL=http://10.9.8.1:18184 docker compose -f compose.a6000-receiver.yaml --profile cutover up -d --force-recreate af3-receiver
docker ps --format '{{.Names}} {{.Status}}'
```

## 5. 公网切流量

私网检查全部通过之后，用户在**阿里云 root 会话**运行：

```bash
bash /home/ecs-user/pskit-agent-cloud-20261002/deploy/agent/scripts/install_host_nginx_agent_cloud.sh
```

脚本先检查本机后端 `127.0.0.1:18088/api/v1/usage` 返回 401，再备份当前 `agent.bioailab.net` vhost、替换 `/api/v1/` 上游，执行 `nginx -t`、reload 和 HTTPS 探针；任一步失败会恢复原配置。验证公网登录与 Refresh Cookie、既有会话、SSE、上传、Token/GPU 额度、`/internal/` 和其他 Supabase 管理路径为 404，且 `https://pskit.bioailab.net/` 仍可用。确认流量已到阿里云本机后端后，停止 A6000 新版 `api-proxy`；A6000 的旧 PSKit 和 AF3 compute 继续运行。

## 回退与数据所有权

回退按**哪个后端已写入 Agent 卷**判断，不按 Nginx 是否切换判断。

1. **阿里云后端尚未启动：** A6000 原卷仍是最新。确认云端无写入后，恢复 A6000 新版后端、本机回调代理与 receiver 默认本机 URL；保持公网原路由。旧云端卷只是备份。
2. **阿里云后端已启动但尚无新写入：** 停云端后端与代理，确认新卷没有新增 Run、消息、额度、任务或 Pi 文件，再恢复 A6000 原卷与 receiver 本机地址。若私网入口已启用，应再禁用该入口。
3. **阿里云已产生任何新写入：** 先停止公网写入和 A6000 receiver，检查 owned-jobs、journal、spool 与计算任务；停云端后端，使用第 3 节同样的快照工具对**云端最新卷**制一致性快照并校验，恢复到**新的 A6000 空卷**，更新 A6000 Compose 卷名，验证会话、余额和任务后才恢复 A6000 后端、本机回调代理与 receiver。最后将公网 Nginx 恢复到 `agent.bioailab.net.conf.pre-aliyun-20261003` 并 reload，再停止云端回调代理。不能直接启动 A6000 旧卷，也不能两地同时领同一个任务。

WireGuard 暂断时，普通聊天仍由阿里云提供；AF3 receiver 在持久 journal/spool 中保留未 ACK 结果，连接恢复后按原任务 ID 重试。实际 AF3 运行中取消与单产物 20 MiB 上限不在本次迁移中改变。

## 部署记录

执行时追加：UTC/北京时间、镜像 digest、源/目标卷名与快照 SHA-256、Run/job/journal 匿名化计数、私网和公网 HTTP 状态、真实 AF3 任务 ID 与状态、Pi 唤醒及旧站检查。**不得记录密码、API key、回调密钥、用户文件或会话文本。**
