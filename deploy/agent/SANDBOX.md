# 用户 CPU 沙箱与 Pi RPC

`compose.sandbox.yaml` 为现有部署添加一个 Docker provider。Python 控制面继续验证 Supabase 身份、当前模型/工具授权、Run 与配额；用户容器运行 Pi 和已注册的受限工具。Pi 0.87.1 的内置 shell、读写、外部 Skills 仍关闭。

## 存储与执行

每个 owner 有一个 `pskit-sbx-<namespace>-<hash>` 容器及独立 `-workspace` 卷。多个会话共享 owner 卷，但每个会话有独立工作目录、transcript 和 Pi 子进程；同会话串行，不同会话遵守后端的全局/用户并发上限。

```text
/workspace/
├── shared/
└── sessions/<session_id>/
    ├── files/                         本轮验证所有权的上传文件
    ├── artifacts/<attempt_id>/        本次执行输出
    ├── attempts/<attempt_id>/          临时执行目录
    └── .pi/                           独立持久 transcript 与 Pi 配置
```

活动 lease、owner、镜像、runtime、revision、排空/替换操作存于现有私有 PostgreSQL schema；先在受信任的数据库网络中使用迁移角色调用 `app.db.postgres_migrations.migrate_postgres(dsn, schema=...)` 升级到当前 schema（现为6），再启动 manager。现有 `stack.sh up` 对默认 schema 使用同一迁移入口；`agent_data_migrate.py` 用于 import/export，不提供原地升级子命令。manager 的线上 factory 必须获得私有 DSN；内存/SQLite activity 替身只在测试中显式注入。

开始 Pi 前 acquire，执行时 renew，仅在 Pi subprocess wait 完成后 release。1800 秒空闲窗口从最后确认完成开始；lease 到期代表 unknown，不证明执行退出。manager 重启后先询问 bridge；bridge 失联或没有对应终态证据时保留未知状态。取消 API 返回 cancelling，只有确认进程退出后为 cancelled。

停止、删除容器和删除卷是不同操作。manager 的 idle sweep 只停止；镜像替换先排空、拒绝新 attempt，活动结束后持久化 claim/revision 并移除旧容器（`v=false`）。新运行实例挂同一个卷。失败操作可重试；禁止删卷解决 owner/image 冲突。metrics 的 CPU core ms 与 RAM 是整个 owner 容器的监控数据，不作为每会话计费重复累加。

## 文件与产物

API 接收 file ID 和 session ID，由后端验证所有权、大小和目录；上传名不决定 Docker 路径。原始字节以 SHA256 验证，通过 `O_NOFOLLOW` 的目录/文件 handle 写入临时文件，再原子发布，冲突或损坏不会覆盖已有文件。只同步该会话获授权的 file ID，不遍历整个用户卷。

产物只导出 `sessions/<session_id>/artifacts/<attempt_id>/`，拒绝 traversal、绝对路径、symlink、目录与超限输出。单文件上限20 MiB、一次导出上限100 MiB/100个文件。输出注册到现有 artifact blob 存储，重传同 owner/session/attempt/name/digest 不重复注册；注册与上传共享实际存储额度的准入锁。现有 `/api/v1/artifacts` 的列表、预览和下载按所有权读取这些产物。

新增 `/api/v1/sandbox/sessions/{session_id}/files` 只接收最多10个 file ID。自动 Agent 执行会同步已绑定在 Run 上的附件，在本轮完成后收集输出并发出 `artifact.created`。模型图片继续使用原图片通路，每轮最多10个总附件。

## 私有网络与凭证

用户容器只接入独立 **internal** sandbox 网络，UID/GID10001、只读根、1 CPU、1 GiB RAM、256 PIDs、无 Docker socket/宿主端口，移除 capabilities 并禁止提权。资源参数由服务器配置提供，浏览器不能传 Docker 参数。

backend 保持原有应用/数据库网络，通过 manager 的认证 bridge proxy 访问 `/v1/pi` 与 `/v1/workspace`。manager 是唯一持有 Docker socket 的基础设施组件。`sandbox-gateway` 连接 app+sandbox，只代理 model、已注册 MCP、Run plan、AF3 与通用 Job 的指定 internal 路径；管理员、worker callback、数据库与其他路径返回403。Python 每次调用仍验证当前 Run token 和用户授权。

server-owned `PYTHONPATH=/app` 使 bridge 从镜像应用路径加载代码及 Pi assets；它不进入 Pi 子进程。Pi 子进程仅继承 PATH/LANG/LC_ALL/TZ，再接收后端绑定的 Run ID、工具 token、模型选项和 workspace 目录。模型 key 必须为 `<run_id>.<tool_token>`；bridge/manager/global provider/Supabase/数据库 secret 不进入 Pi 环境。

gateway 模型请求上限64 MiB；manager bridge body 上限128 MiB，以容纳旧 transcript32 MiB导入与10张最大4 MiB图片的 base64 JSON。环境 allowlist 不等同于不同 UID 或内核隔离；任意 shell/Python 开放仍需单独验收隔离运行时。

## 启用与回退

使用同一代码版本的固定镜像 digest，生产/Staging 必须各自使用 namespace、sandbox 网络、数据库 schema 与私有密钥。以下变量保存在权限0600的服务器 `.env`；不进入前端构建：

```dotenv
AGENT_SANDBOX_IMAGE=registry/pskit-agent@sha256:<固定64位digest>
AGENT_SANDBOX_GATEWAY_IMAGE=nginx@sha256:<经验证的固定digest>
AGENT_SANDBOX_NETWORK=pskit-agent-staging_sandbox
AGENT_SANDBOX_NAMESPACE=staging
AGENT_SANDBOX_MANAGER_TOKEN=<独立随机密钥，至少16字符>
AGENT_SANDBOX_BRIDGE_SECRET=<另一独立随机密钥，至少16字符>
AGENT_SANDBOX_POSTGRES_DSN=<后端同一私有数据库与schema的服务器DSN>
AGENT_SANDBOX_POSTGRES_SCHEMA=pskit
```

叠加 `compose.sandbox.yaml` 后执行 `docker compose ... config --quiet`；确认 backend 不在 sandbox 网络，manager/gateway 没有公开 ports，数据库网络只由受信任组件连接。迁移和 Staging 验收完成后，按照部署流程启用 overlay。本次实现没有修改生产部署。

旧后端 transcript 在下一轮按会话导入 `.pi/`，成功后更新数据库记录。回退到本地 Pi 时，先排空并停止新 Run；通过受信任运维访问保留卷，导出每个会话数据库指向的 transcript，把路径映射到后端 Pi session 目录并核对所有权及内容，再切换 `pi_execution=local`。保留未迁移会话的 sandbox provider 直到迁移完成。禁止 `down -v`。

## 隔离 Docker 验收

`scripts/sandbox_smoke.py` 拒绝生产项目和用户 ID，只接受新的 `pskit-sandbox-test-*` 项目及 `sandbox-test-*` 用户/会话。它启动私有临时 PostgreSQL、gateway、manager 与本地模型 stub，运行真实 Pi；HTTP 从测试 backend 的 app 网络发起，兼容 Docker daemon 不在 WSL localhost 的情况。用户卷保留，退出时仅清理测试容器/网络。

```bash
new_backend/.venv/bin/python deploy/agent/scripts/sandbox_smoke.py \
  --project pskit-sandbox-test-example \
  --image sha256:<本次完整应用镜像ID> \
  --gateway-image nginx@sha256:<固定digest>
```

测试覆盖两用户三会话、文件字节往返、活动超过测试空闲窗口、实际网络/DNS/出口拒绝、排空保卷、停止和 transcript 恢复。独立项目 `pskit-sandbox-test-oct04j` 验收 exit0；沙箱范围45项、真实 PostgreSQL4项通过；最终全后端603项通过，无PostgreSQL skip。模型是本地替身；没有付费推理，也不代表已在生产启用。完整结果及失败记录见 [验收记录](../../docs/research/2026-10-04-user-sandbox-implementation-validation.md)。

科研模型的 Completed/Pending/Failed、用量与额度协议独立于 CPU sandbox，见 [COMPUTE_SERVICES.md](../../new_backend/COMPUTE_SERVICES.md)。
