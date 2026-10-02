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
  --require-incremental-sse \
  --base-url https://agent.bioailab.net \
  --legacy-url https://pskit.bioailab.net \
  --credentials-file private-test-account.json \
  --expected-project-id project-457d4d82d1d5443ba3c4d9736998e2ea-cloud-private-smoke \
  --expected-session-id 5dc51ea0-81bc-4b01-91bd-51e18659b8e9
```

`--require-incremental-sse` 依赖当前测试模型替身识别 `SSE_DELAY_PROBE` 并延迟 0.8 秒输出。切换真实模型网关后应改用可控的延迟探针验收 SSE。

## 回退与未完成项

回退以**谁已写入 Agent 数据**为界，不以公网 Nginx 是否切换为界：

- **A6000 尚未写入：** 先确认 A6000 后端及接收器均未运行、云端源卷没有新增写入。若云端后端尚未停止，维持原服务即可；若已停止，则在确认 A6000 不可能写入后重新启动云端后端与原回调代理，继续使用未改动的云端源卷。保留两端备份，禁止同时启动两个新版后端。
- **A6000 已开始写入：** 即使公网 Nginx 尚未切换，私网 smoke 也会新增会话和消息。先停止 A6000 接收器和后端，检查 AF3 journal、owned-jobs 和进行中的计算；冻结所有可能的写入后，把**最新** A6000 Agent SQLite/Pi 数据制作并校验快照，反向迁入云端新的恢复卷并核对账号/会话。先恢复云端回调入口和唯一接收器，再启动唯一云端后端；公网路由若已切到 A6000，最后以 root 恢复 `.pre-a6000` 虚拟主机并 reload。不能直接使用切换前的旧云端卷启动后端，否则会丢失 A6000 已产生的数据或双写。

真实模型网关仍是私网测试替身；SMTP 尚未配置，邮箱自助注册关闭；Google OAuth 凭据未验证；测试账号默认每日 GPU 0 分钟。因此在完成独立授权与联调前，不宣称真实模型、邮箱注册、Google 登录或真 AF3 自动唤醒已验收。

## 实际切换结果

- 2026-10-02 约 22:30（阿里云时间）完成切换。旧云端新版后端、Web、AF3 回调代理与模型替身均已停止；Supabase、私网 Supabase relay 和旧 `pskit.bioailab.net` 保持运行。阿里云宿主机 Nginx 直接读取 `/var/www/agent.bioailab.net`，不再运行新版前端容器。
- 切换前云端 Agent 数据库有 1 个 `completed` Run、0 个 job、2 条消息、1 个 Pi 会话。停止旧后端后制作的归档 `agent-snapshot.tar.gz` SHA-256 为 `55da1379f6c49ef0eccdedc9503fbefabacf914516b600eb2a252e09b122f124`，含 6 个受 manifest 校验的文件；A6000 校验 SHA-256 与 manifest 后恢复到空卷，旧云端数据卷和归档仍保留。A6000 后端镜像 ID 与云端原镜像同为 `sha256:0ac7743c3c36277ea8e6dd944bd637c4e7612d66d3d08f2a6db088bb595000a6`。
- 阿里云经 WireGuard 的私网 smoke 通过：测试账号登录、迁移项目和会话、Token/GPU 额度、文件上传、Pi 回复和 SSE 完成事件均正常。公网切换并停掉云端 Web 后，完整公网 smoke 又通过两次；`/internal/` 与 Supabase admin 路径均为 404，旧站返回 200。从独立客户端访问 `https://agent.bioailab.net/login` 为 200、TLS 验证结果为 0；未登录 `/api/v1/usage` 为 401。另用模型替身的 `SSE_DELAY_PROBE` 明确延迟输出，在公网 Nginx→WireGuard→A6000 API 链路实测首事件与完成事件相隔 `0.809` 秒；对应测试中的整段缓冲响应会失败。JS/CSS 构建资源在停掉前端容器后仍各返回 200。最后核对 A6000 数据库为 5 个 `completed` Run、0 个 job、10 条消息、5 个 Pi 会话。
- 旧 A6000 AF3 接收器已停止，本机接收器 `pskit-af3-receiver-local-20261002` 是唯一运行中的接收器，`RestartCount=0`；原计算容器持续运行，journal 与后端 owned-jobs 均为空。接收器首次启动时发现旧容器使用 `python3` entrypoint，而新 Compose 直接执行无可执行位的脚本；补齐同样的 entrypoint 并通过回归测试后启动。A6000 本机回调无密钥返回 404。阿里云旧回调 Nginx 配置备份为 `.disabled-20261002`，`10.9.8.1:18184` 已停止监听；公网网站和 API 在停用后再次通过 smoke。未执行数据回退。
- A6000 上 `timedatectl status` 报告时钟未同步；两机读取 UTC 时间相差约 47 秒。`timedatectl timesync-status` 显示当前选择 IPv6 NTP 地址且 `Packet count: 0`，推测它没有收到校时响应。当前登录与 Pi smoke 通过，但应配置可达的校时源并确认同步后再依赖跨主机时间进行租约或审计。
- 仍使用模型网关替身；SMTP 未配置、自助邮箱注册关闭，Google OAuth 未验收；GPU 默认每日 0 分钟，未提交真实 AF3 任务，故不能声称真实 AF3 计算、GPU 结算或 Pi 自动唤醒已验收。
