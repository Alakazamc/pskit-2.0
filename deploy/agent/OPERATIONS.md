# 阿里云独立部署

新版使用 `pskit-agent-supabase` 和 `pskit-agent-cloud` 两个 Compose 项目，配置与数据不能复用旧 PSKit 或 WSL 测试环境。云端 Docker 数据目录是 `/data/docker`；数据库、Storage 和 Agent 数据使用独立 Docker 命名卷。不要执行 `docker compose down -v`。

## 私网阶段

将 `infra/supabase`、`deploy/agent` 放在阿里云独立目录。Supabase 从 `.env.example` 创建权限 `0600` 的 `.env`，运行 `utils/generate-keys.sh --update-env` 和 `utils/add-new-auth-keys.sh --update-env` 生成全新密钥；两脚本会打印密钥，执行时须重定向输出。设置 `cloud.env.example` 中的域名、端口和登录开关，保留 `CLOUD_DISABLE_SIGNUP=true`、`ENABLE_EMAIL_AUTOCONFIRM=false`。真实 SMTP 缺失期间只创建管理测试账号，不能开放注册。

应用在 `deploy/agent` 建立权限 `0600` 的 `.env`、`cloud.backend.env`、`cloud.proxy.env`。后两者使用同一枚新生成的 AF3 回调密钥，绝不能复用 WSL 测试密钥。Supabase publishable key 只放 Python 后端配置；service key 留在 Supabase 内。私网阶段使用 `compose.yaml`、`compose.local.yaml`、`compose.cloud.yaml` 顺序叠加并启用 `--profile private-test`，模型替身使用已经传入的后端镜像；正式运行前移除 local overlay 和 private-test profile，配置真实模型网关。默认游客和会员 GPU 额度均为 0。

启动前用 `docker compose config --quiet` 检查配置，核对所有宿主机端口只绑定 `127.0.0.1`。先启动 Supabase，再启动应用。检查 `127.0.0.1:18085`、`127.0.0.1:18088/health/ready`，以及无密钥时 `127.0.0.1:18185` 的拒绝响应。不要将这些端口直接开放到公网。

## 公网域名与邮件：需用户以 sudo 执行

把 `agent.bioailab.net` 的 A 记录指向 `47.121.29.141`，用现有 ACME 工作流签发该域名证书。证书文件存在后，将 [`host-nginx-agent.conf`](host-nginx-agent.conf) 安装为新的虚拟主机；保留旧 `pskit.bioailab.net` 文件：

```bash
sudo install -m 0644 host-nginx-agent.conf /etc/nginx/conf.d/agent.bioailab.net.conf
sudo nginx -t
sudo systemctl reload nginx
curl -I https://agent.bioailab.net/
curl -I https://pskit.bioailab.net/
```

真实 SMTP 主机、端口、用户、密码及发件人填写到云端 Supabase `.env`（权限 `0600`），之后才将 `CLOUD_DISABLE_SIGNUP=false` 并验证邮箱验证码全流程。Google 登录另需提供者凭据及真实回调验证。公网检查 `/internal/` 为 404、登录 Refresh Cookie 带 `Secure`，并确认旧站仍可访问。

## A6000 私网 AF3：需先排查 WireGuard

阿里云为 `10.9.8.1`，A6000 为 `10.9.8.2`。此前双方 WireGuard 地址上的 TCP 超时。两端先运行 `sudo wg show`、`ip route get <对端地址>` 和 `sudo ufw status verbose`（或实际防火墙命令），核对 AllowedIPs、路由和接口。确认云端确有 `10.9.8.1` 后，把 [`host-nginx-af3.conf`](host-nginx-af3.conf) 安装为独立私网监听：

```bash
sudo install -m 0644 host-nginx-af3.conf /etc/nginx/conf.d/agent-af3-private.conf
sudo nginx -t
sudo systemctl reload nginx
sudo ufw allow in on wg0 from 10.9.8.2 to 10.9.8.1 port 18184 proto tcp
```

最后一条只在 UFW 已启用且接口确为 `wg0` 时执行。Nginx 应只在 `10.9.8.1:18184` 接收 `10.9.8.2`；Docker 代理仍只发布 `127.0.0.1:18185`。无需 `10.9.8.3` 转发。

切换前确认旧 WSL 队列和 A6000 receiver journal 无未完成任务，备份原 receiver 配置，只重建 receiver，保留 compute 容器和 spool。新 receiver 使用云端 URL、新回调密钥和 `a6000-af3-cloud-1`。先做带密钥只读 owned-jobs 请求，再用云端测试账号运行一个低成本真 AF3，核对租约、进度、`simulation=false`、产物、GPU 扣费、ACK、spool 清理及 Pi 自动唤醒。失败时保留云端 claim 与 A6000 spool 以便对账，勿把任务重放到旧 WSL 后端。

公开 AF3 前仍须处理运行中任务取消不能立即停 GPU、单件产物 20 MiB 上限。替换镜像或迁移前备份 Supabase DB 卷与 Agent 数据卷；若新 Nginx 配置出错，只删除新的两个配置文件并 `nginx -t`、reload，不动旧站。
