# 新版 PSKit 后端迁至 A6000：执行记录

本记录只涉及 `agent.bioailab.net` 的新版。旧 `pskit.bioailab.net`、旧 PSKit 容器及阿里云 Supabase 数据卷不迁移。当前目标是阿里云宿主机 Nginx 直接提供已构建的 React `dist`、HTTPS 和 Supabase；A6000 提供 Python/Pi、Agent 数据和 AF3。前端无需常驻容器。

## 已确认的机器与边界

| 项目 | 阿里云 | A6000 |
| --- | --- | --- |
| WireGuard | `10.9.8.1` | `10.9.8.2` |
| 新版公网入口 | `agent.bioailab.net` 的宿主机 Nginx | 无公网端口 |
| 前端 | 宿主机 Nginx 的 `/var/www/agent.bioailab.net` 静态目录 | 无 |
| Python/Pi | 切换前 `127.0.0.1:18088`，切换后停止 | `127.0.0.1:18089`，私网代理 `10.9.8.2:18088` |
| Supabase | Envoy `127.0.0.1:18130`，受限私网转发 `10.9.8.1:18130` | 无本地 Supabase |
| AF3 回调 | 切换前 `10.9.8.1:18184` | 切换后 `127.0.0.1:18185` |

固定镜像：阿里云后端 `pskit-agent-backend:cloud-20261002-0ac7743c`，原镜像 ID `sha256:0ac7743c3c36277ea8e6dd944bd637c4e7612d66d3d08f2a6db088bb595000a6`；前端原镜像 `pskit-agent-web:cloud-20261002-44879324`，原镜像 ID `sha256:44879324a06d6ac90560dca3663f394fb0390612cf458400c22f839159e70f89`。A6000 AF3 接收器和计算容器目前均使用 `af3_mar5_jhli_2026_0923:v1`。

## 切换前证据

- 阿里云原后端：`agent_runs` 只有 1 条 `completed`，`agent_jobs` 为 0，`agent_messages` 为 2，`pi_sessions` 为 1。此为预检时状态；**切换前必须重新检查**。
- A6000 原接收器 `pskit-af3-receiver-cloud-20261002` 的 journal `jobs` 为 0；切换前须再次核对后端 owned-jobs 与计算容器。
- Supabase 私网转发使用独立 Docker Compose 项目 `pskit-agent-supabase-relay`，与已批准的 Nginx ACL 配置相同，免去 `ecs-user` 不可用的 sudo。A6000 访问 `10.9.8.1:18130/auth/v1/health` 返回 401（请求通过 ACL，Envoy 要求 API key）；阿里云从非许可源地址访问返回 403。
- 阿里云原新版后端、Web、Supabase 和旧 `pskit` 仍在运行。上述检查**不代表切换已完成**。
- 当前固定版 Web 容器的 `/usr/share/nginx/html` 已暂存到阿里云 `frontend-dist`，包含 376 个文件、约 15 MiB；宿主机 Nginx 切换前再核对文件 SHA-256。

## 切换门槛与顺序

1. 确认 A6000 后端镜像 ID 与云端一致，A6000 Compose、回调代理和 API 代理配置可渲染；只允许后端通过私网连接原 Supabase。私网代理要同时受 Nginx ACL 和宿主机防火墙约束。
2. 用旧后端只读数据库查询确认没有 `running`/`resume_queued` Agent Run、没有 `queued`/`running` AF3 job；A6000 原 receiver journal 为 0，compute 没有活动计算。暂停原接收器领任务，停止阿里云新版后端与回调代理。保留云端 Web、Supabase 和旧 PSKit。
3. 按 [OPERATIONS.md](OPERATIONS.md) 的快照命令迁移 `pskit-agent-cloud_agent_data`，归档 SHA-256 跨主机核对，在空的 `pskit-agent-a6000_agent_data` 卷恢复并设置 `10001:10001`。Supabase 的数据库、Storage 不复制。
4. 在 A6000 启动唯一新版后端、私网 API 代理和本机 AF3 回调代理。先从阿里云经 WireGuard 验证登录、原项目/会话、Token/GPU 额度、SSE 与上传，再停止旧 receiver、以 `cutover` profile 启动本机回调 receiver。保持原 compute 容器与 spool。
5. 阿里云从当前前端镜像提取已构建的 `dist`，以 root 放入 `/var/www/agent.bioailab.net`；公网 Nginx 直接服务静态文件、仅允许 OAuth 必需路径到本机 Supabase、`/api/v1/` 到 A6000。阿里云 `ecs-user` 没有可用 sudo，需通过云助手 root 执行下面这一条。脚本先确认 A6000 私网 API 返回 401，再**备份并替换** `/etc/nginx/conf.d/agent.bioailab.net.conf`，执行 `nginx -t`、reload、HTTPS `/login` 检查；语法或 reload 失败时恢复原虚拟主机：

   ```bash
   bash /home/ecs-user/pskit-agent-cloud-20261002/deploy/agent/scripts/install_host_nginx_agent.sh
   ```

6. 运行 `tests/smoke_split.py`，从公网核对旧站仍可访问、`/internal/` 和 Supabase admin 路径为 404、登录、原消息、上传、额度、Pi/SSE；核对代理访问日志中的 A6000 后端流量。成功后停止阿里云原 Web 容器，确认 `agent.bioailab.net/login` 仍返回 200；前端不再需要容器。只有获授权的测试账号 GPU 额度大于 0 时才做单个真 AF3：检查 claim、进度、`simulation=false`、产物、GPU 结算、结果 ACK、journal/spool 清理和 Pi 自动唤醒。切换成功后停用旧 `10.9.8.1:18184` 回调入口。

示例探针，测试账号文件已在阿里云权限 `0600` 的 `private-test-account.json` 中；从阿里云部署目录执行，不输出文件内容：

```bash
python3 deploy/agent/tests/smoke_split.py \
  --base-url https://agent.bioailab.net \
  --legacy-url https://pskit.bioailab.net \
  --credentials-file private-test-account.json \
  --expected-project-id project-457d4d82d1d5443ba3c4d9736998e2ea-cloud-private-smoke \
  --expected-session-id 5dc51ea0-81bc-4b01-91bd-51e18659b8e9
```

## 回退与未完成项

公网路由切换前失败：保持云端原后端和 Web 运行，停止 A6000 预备容器，保留备份。切换后 A6000 已产生新数据时，先冻结 A6000 写入，检查 AF3 journal 与后端任务状态，将**最新** Agent SQLite/Pi 数据反向迁移，再恢复云端后端和旧公网 Nginx 配置；不能直接启动云端旧后端造成双写。

真实模型网关仍是私网测试替身；SMTP 尚未配置，邮箱自助注册关闭；Google OAuth 凭据未验证；测试账号默认每日 GPU 0 分钟。因此在完成独立授权与联调前，不宣称真实模型、邮箱注册、Google 登录或真 AF3 自动唤醒已验收。

## 实际切换结果

待执行后填写：数据快照 SHA-256、镜像 ID、部署时间、数据库/会话计数、私网和公网探针结果、旧站状态、真 AF3 结果及任何回退动作。
