# 新版 PSKit：单 PostgreSQL 切换与回退

本手册适用于阿里云 `pskit-agent-supabase` 的 PostgreSQL 17、阿里云 Python/Pi、阿里云 LiteLLM，以及 A6000 上唯一的 AF3 接收器。前端由阿里云宿主机 Nginx 提供 `dist`。旧 `pskit.bioailab.net` 不参与切换。

**当前状态：本文件是待执行手册。下面的本地替身记录不能当作生产验收。** 在候选模型的真实提供商 API key、受控 AF3 任务和阿里云 root 操作完成前，不停 A6000 生产写入，也不切公网 Nginx。任何阶段只有一个 Agent 后端可以写业务数据。所有命令必须在注明的主机运行；不要执行 `down -v`，不要删除旧 PG16 卷、A6000 Agent 卷或原始快照。

## 0. 路径、凭据和容量

- 阿里云工作目录：`/home/ecs-user/pskit-agent-cloud-20261002`；A6000 工作目录：`/data/jhli/pskit-agent-a6000-20261002`。执行前用 `pwd`、`docker compose config --quiet` 和 `docker volume inspect` 重新确认实际目录、项目名和卷名。
- 阿里云 `infra/supabase/.env`、新 `infra/litellm/.env.shared`、旧 `infra/litellm/.env`、`deploy/agent/.env.stack-admin`、`cloud.env`、`cloud.backend.env`、`cloud.proxy.env` 均须是 0600；目录和快照目录为 0700。`.env.stack-admin` 包含 `SHARED_POSTGRES_ADMIN_DSN`、`PSKIT_DB_PASSWORD`、`LITELLM_DB_PASSWORD`，另为离线导入/导出写入同值的 `RESEARCH_AGENT_DATABASE_URL`。两条 DSN 都指向 Docker 内的 `db:5432/postgres`，只在 0600 文件中出现；后端的 DSN 必须使用受限 `pskit_app`。
- `cloud.env` 的 `AGENT_PG_DATA_VOLUME` 指向一个**全新且为空**的卷，例如 `pskit-agent-cloud-pg17-20261003_agent_data`。不能把旧阿里云 Agent 卷或准备过的 return 卷当成空卷。`MODEL_GATEWAY_API_KEY` 是候选库产生的**新虚拟 key**，不是旧 `.pskit-virtual-key`。
- 阿里云检查 `df -h /data/docker`、`docker system df`、PostgreSQL 连接数 `SELECT count(*) FROM pg_stat_activity` 与 `SHOW max_connections`；备份目录可用空间至少覆盖当前 Supabase DB、旧 LiteLLM DB、A6000 Agent 卷及传输副本。`ss -lnt` 核对数据库 5432 未公开，后端只在 `127.0.0.1:18088`，LiteLLM 只在 WireGuard `10.9.8.1`。
- 切换时保留源卷、快照、旧镜像、旧 LiteLLM 4000 网关与 PG16；遇到不确定状态立即停止本阶段，保留 journal/spool，不重放 AF3 任务。

## 1. 备份 PostgreSQL、LiteLLM 与旧数据卷

阿里云先建立私有备份目录。下例 `supabase_compose` 仅是当前固定 Compose 的缩写；实际运行前确认它选中了 `pskit-agent-supabase` 而非旧 PSKit 项目。

```bash
cd /home/ecs-user/pskit-agent-cloud-20261002
umask 077
mkdir -p backups/single-postgres
supabase_compose() {
  docker compose --env-file infra/supabase/.env \
    -f infra/supabase/docker-compose.yml -f infra/supabase/compose.cloud.yaml \
    -p pskit-agent-supabase "$@"
}
supabase_compose config --quiet
supabase_compose exec -T db sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" pg_dumpall --globals-only -U postgres' \
  > backups/single-postgres/roles.sql
supabase_compose exec -T db sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -Fc -d postgres -U postgres' \
  > backups/single-postgres/postgres.dump
supabase_compose exec -T db sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -Fc -d litellm -U postgres' \
  > backups/single-postgres/litellm.dump
docker run --rm --network none -v "$PWD/backups/single-postgres":/backup:ro \
  supabase/postgres:17.6.1.136 pg_restore -l /backup/postgres.dump > /dev/null
docker run --rm --network none -v "$PWD/backups/single-postgres":/backup:ro \
  supabase/postgres:17.6.1.136 pg_restore -l /backup/litellm.dump > /dev/null
sha256sum backups/single-postgres/* > backups/single-postgres/SHA256SUMS
```

`roles.sql` 含角色凭据哈希，绝不能发到聊天、日志或 Git。首次备份 `litellm` 前须先完成下一节的空库创建；若尚未创建，先备份 `postgres` 和角色，在候选初始化后补 `litellm.dump`。验证备份非空、`sha256sum -c` 成功，另做一份异机副本。旧 LiteLLM PG16 运行中先用旧 Compose 的 `db` 执行同样的 `pg_dump -Fc -d litellm` 到**另一份**旧库备份；待旧 gateway/db 停止后再只读归档其 `litellm-postgres` 命名卷。不要对运行中的 PG16 数据目录做裸 `tar` 作为唯一备份。

A6000 的 `pskit-agent-a6000_agent_data` 是**最新 Agent 数据源**；阿里云旧 `pskit-agent-cloud_agent_data` 只是过期副本。停止源写入后在 A6000 用下述快照工具备份 Agent SQLite/WAL 和 Pi transcript，并在另一主机保存原始归档；不要改变源卷。阿里云旧 Agent 卷也以只读 `docker run -v 卷名:/source:ro ... tar` 单独归档供历史回退取证。

## 2. 候选 LiteLLM 4001 与新密钥

先保持旧 LiteLLM 4000、旧 PG16 和 A6000 后端在线。新库只在 Supabase PG17 实例中创建，不启动第二个 PostgreSQL 容器。在阿里云用从已提交源码构建、标记为 `pskit-agent-backend:pg17-20261003-r1` 的固定版后端镜像和私有 admin env 运行 `provision_shared_postgres.py`；先记录该镜像的 digest，确认与构建记录一致。确认 `pskit_app` 无权读 `auth.users`，`litellm` 用户只在独立 `litellm` 逻辑数据库建表。候选配置 `.env.shared` 临时写 `LITELLM_PUBLIC_PORT=4001`，master/salt 为新值且保持 0600。

```bash
cd /home/ecs-user/pskit-agent-cloud-20261002
python3 deploy/agent/scripts/prepare_single_postgres_candidate.py "$PWD"
test "$(stat -c %a deploy/agent/.env.stack-admin)" = 600
test "$(stat -c %a infra/litellm/.env.shared)" = 600
docker run --rm --network pskit-agent-supabase_default \
  --env-file deploy/agent/.env.stack-admin --read-only --cap-drop ALL \
  -v "$PWD/deploy/agent/scripts/provision_shared_postgres.py":/app/provision_shared_postgres.py:ro \
  --entrypoint python pskit-agent-backend:pg17-20261003-r1 \
  /app/provision_shared_postgres.py
docker compose --env-file infra/litellm/.env.shared \
  -f infra/litellm/compose.shared-postgres.yaml \
  -p pskit-agent-litellm-candidate up -d --wait
cd infra/litellm
python3 bootstrap_pskit.py --base-url http://10.9.8.1:4001 \
  --env-file .env.shared --key-file .pskit-candidate-virtual-key
```

在 `http://10.9.8.1:4001/ui` 用**新** master key 重新添加提供商 API key 与模型部署，公开名称至少包含 `claude-opus-4-8`。用户已决定旧模型/API key 不迁移。确认团队 **10 美元/30 天**、单用户 **2 美元/30 天**预算；在候选网关以新 key 验证 `/v1/models`、一次真实模型调用、工具调用与流式响应，核对 Admin UI 用量。只记录状态、模型别名与用量，不记录 key 或聊天文本。先运行 `bootstrap_pskit.py` 第二次确认新虚拟 key 被该候选库验证；旧 key 对候选库应拒绝。

**候选模型和新虚拟 key 未通过，旧 LiteLLM 4000 不能停止。** 切换前备份新 `litellm` 逻辑数据库和旧 PG16 数据库，确认两份不同；旧 PG16 卷继续保留。

## 3. A6000 停写、AF3 清空与一致性快照

在 A6000 检查 `agent_runs`、`agent_jobs`、接收器 `journal.sqlite3`、后端 `owned-jobs`、spool 中未 ACK 结果和计算容器。`queued`、`running`、`waiting`、`resume_queued` 或未知状态都要等待或对账；不能通过强制删 journal/spool 清场。需确认 GPU 实际空闲，接收器停止领新任务。冻结公网新增 Agent 写入，然后暂停**唯一** A6000 receiver，停止 A6000 后端与本机代理；旧 LiteLLM 可继续服务但不能再有 Agent 写入。

```bash
cd /data/jhli/pskit-agent-a6000-20261002/deploy/agent
docker ps --filter name=pskit-af3 --format '{{.Names}} {{.Status}}'
docker stop pskit-af3-receiver-local-20261002
docker compose --env-file .env -f compose.a6000.yaml stop api-proxy af3-callback-proxy backend
umask 077
mkdir -p backups
docker run --rm --network none --user 0:0 \
  -v pskit-agent-a6000_agent_data:/source:ro -v "$PWD/backups":/backup \
  -v "$PWD/scripts/agent_data_snapshot.py":/snapshot.py:ro \
  pskit-agent-backend:litellm-20261003 \
  python /snapshot.py snapshot /source /backup/a6000-stopped --source-stopped
docker run --rm --network none --user 0:0 \
  -v "$PWD/backups/a6000-stopped":/snapshot:ro \
  -v /data/jhli/pskit-af3-receiver-test-20261002/spool:/receiver:ro \
  -v "$PWD/scripts/check_single_postgres_quiescence.py":/gate.py:ro \
  pskit-agent-backend:litellm-20261003 \
  python /gate.py /snapshot/agent.sqlite3 /receiver/journal.sqlite3
tar -C backups -czf backups/a6000-stopped.tar.gz a6000-stopped
(cd backups && sha256sum a6000-stopped.tar.gz > a6000-stopped.tar.gz.sha256)
```

门禁必须输出 `runs=0, jobs=0, journal=0`。还须以接收器的回调密钥发只读 `owned-jobs` 请求确认 0，确认 compute 容器无任务、spool 无未 ACK 结果；脚本无法替代这三个外部检查。若门禁失败，保持 A6000 写入冻结，查明状态后重新快照，绝不进入导入。传输归档到阿里云的 0700 私有目录，执行 `sha256sum -c a6000-stopped.tar.gz.sha256` 后解压，检查清单及 `agent.sqlite3` 完整性。源卷和原始包保留。

## 4. 导入 `pskit`、切网关、私网验收

阿里云先确认新的 Agent Docker 卷为空，且只有 A6000 原后端曾是写者。`cloud.env` 的 `AGENT_PG_DATA_VOLUME` 必须指向该新卷。下面例子以 `/home/ecs-user/pskit-agent-cloud-20261002/backups/single-postgres/a6000-stopped` 为已校验快照目录；离线导入容器只读挂载快照与 Pi 卷，管理 DSN 只从 `.env.stack-admin` 读取。

```bash
cd /home/ecs-user/pskit-agent-cloud-20261002
docker volume create pskit-agent-cloud-pg17-20261003_agent_data
docker run --rm --network none --user 0:0 \
  -v pskit-agent-cloud-pg17-20261003_agent_data:/data \
  -v "$PWD/backups/single-postgres/a6000-stopped":/snapshot:ro \
  --entrypoint sh pskit-agent-backend:pg17-20261003-r1 \
  -c 'python /app/scripts/agent_data_migrate.py copy-pi /snapshot /data/pi-sessions && chown -R 10001:10001 /data/pi-sessions'
docker run --rm --network pskit-agent-supabase_default \
  --env-file deploy/agent/.env.stack-admin \
  -v "$PWD/backups/single-postgres/a6000-stopped":/snapshot:ro \
  -v pskit-agent-cloud-pg17-20261003_agent_data:/data:ro \
  --entrypoint python pskit-agent-backend:pg17-20261003-r1 \
  /app/scripts/agent_data_migrate.py import /snapshot/agent.sqlite3
```

核对导入器的表数/行数、源 SHA-256、Pi 文件清单、原用户项目/会话/消息/文件/AF3 产物、Token/GPU 余额。检查 Supabase `auth` 与 `storage` 未变化，旧 `litellm` 数据未导入。若出错，正式 `pskit` schema 保持未激活；修复后用新 staging schema 重试，绝不启动后端。

候选通过且导入验证成功后，执行 **旧 LiteLLM 4000 停止**：先停候选 4001，停旧 `pskit-agent-litellm` gateway/db，备份已停止的旧 PG16 卷但不删除；将 `.env.shared` 的 `LITELLM_PUBLIC_PORT` 改为 `4000`，保留同一新 master/salt/数据库，后端使用候选阶段验证过的新虚拟 key。`deploy/agent/stack.sh up` 会按 Supabase → 账号 → LiteLLM → schema → backend/proxy 的顺序启动。`stack.sh status` 必须显示 `supabase-db: 1` 与 `legacy-litellm-db: 0`；Python readiness 为 200，`127.0.0.1:18088/api/v1/usage` 未登录为 401，回调代理无密钥为 404。不要启动 `web` 容器，旧 `pskit.bioailab.net` 必须仍健康。

私网用既有账号检查登录、原历史会话、Pi 续聊、文件上传/下载、Token 与 GPU 余额、LiteLLM 实际调用、SSE 重连、内部 `/internal/` 拒绝。只在后端与 A6000 回调链路都健康且领取门禁再次为零后，由阿里云 root 会话运行 `enable_private_af3_ingress.sh`；A6000 以 `AGENT_AF3_API_URL=http://10.9.8.1:18184` 重建**同一个** receiver，确认只有一个接收器、journal 不增、owned-jobs 与新后端一致。经测试账号预留足够 GPU 配额后，仅执行一次受控真实 AF3：核对 `simulation=false`、claim/进度、产物哈希、GPU 结算、ACK、spool 清理及 Pi 自动唤醒。若任一环节失败，保留原卷/快照并按写入阶段回退。

只有以上实际证据齐全，通知用户在阿里云 root 会话运行 `bash /home/ecs-user/pskit-agent-cloud-20261002/deploy/agent/scripts/install_host_nginx_agent_cloud.sh`。脚本先检查本机私网 API，再备份原虚拟主机、切 `/api/v1/` 到 `127.0.0.1:18088`，失败自动恢复。公网复核 `https://agent.bioailab.net/login`、HTTPS 证书、登录/上传/SSE、`/internal/` 为 404、旧 `https://pskit.bioailab.net/`；Nginx 不暴露 5432、18088、18185。

## 未产生新写入时的回退

若新 PostgreSQL 后端尚未产生任何项目、消息、配额、任务或 Pi 写入，停止阿里云 Agent 后端与回调代理，恢复 A6000 原后端和本机回调、唯一原 receiver；让旧 LiteLLM gateway 重新指向保留的 PG16 卷及旧 key。若公网已切换，root 用 `agent.bioailab.net.conf.pre-aliyun-20261003` 恢复原 Nginx 配置并 `nginx -t`、reload。此路径仅适用于确证**零新写入**。不要删除新 PostgreSQL schema 或快照。

## 已产生新写入后的回退

先冻结公网 Agent 新请求，暂停 A6000 receiver 领任务；核对后端 `owned-jobs`、receiver `journal`、spool、运行中 GPU 和未结算配额。未 ACK 的结果先对账，不重放任务。停止云端后端写入，在阿里云用受限的 0600 管理 env 运行 `agent_data_migrate.py export`，将最新 `postgres.pskit` 导出到**全新**的 `agent.sqlite3` 文件，并校验 SQLite integrity/hash/表数/账本顺序；同时从当前云端 Agent 卷复制最新 **Pi transcript** 并按清单校验。旧快照不能代替最新导出。

```bash
cd /home/ecs-user/pskit-agent-cloud-20261002
umask 077
mkdir -m 700 backups/single-postgres/rollback-new-writes
docker run --rm --network pskit-agent-supabase_default \
  --env-file deploy/agent/.env.stack-admin \
  -v "$PWD/backups/single-postgres/rollback-new-writes":/export \
  --entrypoint python pskit-agent-backend:pg17-20261003-r1 \
  /app/scripts/agent_data_migrate.py export /export/agent.sqlite3
docker run --rm --network none --user 0:0 \
  -v pskit-agent-cloud-pg17-20261003_agent_data:/source:ro \
  -v "$PWD/backups/single-postgres/rollback-new-writes":/export \
  --entrypoint sh pskit-agent-backend:pg17-20261003-r1 \
  -c 'cp -a /source/pi-sessions /export/pi-sessions'
(cd backups/single-postgres/rollback-new-writes && \
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
```

若 `rollback-new-writes` 已存在，换一个全新目录；不能覆盖上次导出。对 `agent.sqlite3` 执行 `PRAGMA integrity_check`，与 PostgreSQL 的 36 张业务表逐表比较行数、关键 ID 与账本合计，验证 `SHA256SUMS` 后再传回 A6000。上传、模型和 AF3 回调仍须保持停写，直到旧后端从**新卷**启动且核对完成。

把新的 SQLite 和 Pi 文件传回 A6000，恢复到一个**新卷**，绝不覆盖原 A6000 源卷；A6000 Compose 以临时覆盖文件改用新卷。校验用户/项目/会话/AF3 状态、Token/GPU 余额和 Pi 续聊，再恢复唯一 A6000 后端、本机回调与 receiver。仅此之后，若公网已切换，阿里云 root 恢复 `agent.bioailab.net.conf.pre-aliyun-20261003` 并 reload；停新 LiteLLM 后可用旧 PG16 gateway 与旧 key。**启动旧后端**之前必须完成上述冻结、对账、反向导出和新卷校验。保留两边数据库、卷、journal、spool、密钥文件与日志供后续审计。

## 证据记录（只记计数和状态）

| 检查项 | 本地替身结果 | 阿里云/A6000 实际结果 |
| --- | --- | --- |
| 固定版 PG17、LiteLLM gateway 健康 | 本地候选 readiness/UI 均 200 | 待执行 |
| 新预算/虚拟 key、模型工具流 | 本地 10/2 美元预算；mock 工具流 200；旧测试 key 被拒绝 | 待用户在候选 UI 配置真实提供商 |
| SQLite→PG→SQLite 与 Pi 清单 | 本地迁移回归通过，36 表样本与字节哈希保留 | 待 A6000 停写后核对实际计数 |
| Run/job/journal/owned-jobs/GPU | 本地门禁与回调契约测试 | 待生产读取与受控真 AF3 |
| 公网 HTTPS、SSE、上传、旧站 | 尚未在新拓扑切换 | 待私网验收、root 脚本执行后记录 |

记录时间、镜像 digest、源/目标卷名、快照 SHA-256、匿名化行数与状态、HTTP 状态、真 AF3 job ID 和 ACK 状态。不得记录密码、API key、回调密钥、文件内容或聊天文本。
