# OpenSandbox / gVisor 工作区运行手册

本文描述 PSKit 工作区执行面。Pi RPC 仍在 Python 后端内运行；用户代码只通过受控工作区工具进入 OpenSandbox。每个用户拥有一个 gVisor 沙箱和一个持久卷，会话目录固定为 `/workspace/<session_id>/`。

## 固定版本与制品

| 组件 | 固定版本 | 发布要求 |
| --- | --- | --- |
| Python SDK | `opensandbox==1.1.0` | `new_backend/pyproject.toml` 锁定 |
| OpenSandbox Server | `1.1.0`，上游提交 `b1a29cf93a823a95913f7943010febb3f29de05c` | 固定 base digest 构建派生镜像并记录最终 digest |
| execd | `1.1.0` | 私有 TOML 写完整 digest |
| egress | `1.1.7` | 私有 TOML 写完整 digest；gVisor 模式当前不启用 sidecar |
| workspace | PSKit 固定版本 | Python base 和最终镜像均记录 digest |

派生 Server 只做两项可审计修订：诊断输出实际 OCI runtime；Docker PVC 必须由指定的非 `local` 配额卷驱动创建，并验证 `size` 与 `inodes` 选项。补丁遇到非 1.1.0 源码形状会中止构建。

## 宿主机前置条件

1. Docker 已注册 `runsc`，`docker info` 的 Runtimes 包含 `runsc`。
2. 已安装并启用能对持久卷同时执行字节和 inode 硬限制的 Docker volume plugin。普通 `local` named volume 不合格。
3. 配额驱动接受 `size=<IEC size>` 与 `inodes=<positive integer>` 两个 create 选项，并在重启后保留卷。
4. 创建权限为 `0600` 的环境文件和 OpenSandbox TOML；API key 至少 32 字符，且不进入 Git。
5. 四个运行镜像都已解析成 `name@sha256:<64 hex>`，禁止 `latest` 与仅 tag 引用。

OpenSandbox Server 持有 Docker socket，因此属于受信任控制面。只有它挂载 socket；backend、web 和用户沙箱均不得挂载。Server 没有 host `ports`，只加入内部 `app` 控制网络和独立 Docker `internal` runtime 网络。

### 受控注册 gVisor

先在可信环境下载已经审阅的 `runsc` 固定版本及其 SHA256，不使用移动的
`latest` 地址。首次只预览 Docker 配置差异：

```bash
bash deploy/agent/scripts/install_gvisor_runtime.sh \
  --package /path/to/runsc --sha256 '<release sha256>' --version '<fixed version>'
```

确认差异后，root 运维人员可用相同参数加 `--apply`。脚本安装带版本号的
二进制、备份已有 `/etc/docker/daemon.json`，并且只合并 `runsc` runtime；
脚本不会 reload 或 restart Docker。执行宿主机认可的 reload 前，先记录当前
AF3、CORAL、Supabase、LiteLLM 和 backend 容器。之后运行：

```bash
bash deploy/agent/scripts/check_gvisor_runtime.sh \
  '<fixed probe image>@sha256:<digest>' '<fixed runsc version>'
```

探针关闭网络，使用只读根文件系统、移除全部 Linux capabilities 并启用
`no-new-privileges`。注册失败时恢复脚本输出的
`daemon.json.pre-runsc-*` 备份，再通过同一宿主机流程 reload Docker。恢复时
保留现有容器和卷，不执行 `docker compose down -v`。

## 构建与私有配置

```bash
docker build \
  --build-arg OPENSANDBOX_SERVER_BASE='opensandbox/server@sha256:<digest>' \
  -t pskit-opensandbox-server:<release> \
  deploy/agent/opensandbox-server

docker build \
  --build-arg PYTHON_BASE_IMAGE='python:3.12-slim@sha256:<digest>' \
  -t pskit-workspace:<release> \
  deploy/agent/sandbox
```

构建后用 `docker image inspect IMAGE --format '{{index .RepoDigests 0}}'` 记录最终 digest。把 `opensandbox.toml.example` 复制到部署私有目录，替换 execd、egress digest 和环境对应的 runtime network。把 `cloud.env.example` 中 OpenSandbox 字段复制到私有 `.env`；`OPENSANDBOX_VOLUME_DRIVER` 填已验收的配额驱动名。TOML 的 network name 必须与 `.env` 完全相同。

## 失败关闭预检

静态预检不会创建用户沙箱：

```bash
python deploy/agent/scripts/opensandbox_preflight.py \
  --env-file deploy/agent/.env \
  --config /path/to/opensandbox.private.toml \
  --compose-file deploy/agent/compose.yaml \
  --compose-file deploy/agent/compose.cloud.yaml \
  --compose-file deploy/agent/compose.opensandbox.yaml
```

启动 Server 后增加 `--live`。Live probe 从当前 backend 镜像启动一次性客户端，真实创建 `runsc` 沙箱，检查 UID/GID 10001、能力清零、NoNewPrivs、PID 1 环境不可读、Docker socket/后端源码/控制面 DNS 不可见、无公网连接、事件、取消、metrics 和 stop 后持久卷重连。输出只有 capability 布尔值和公开标识，不打印 key、命令正文或文件正文。

任一 digest、`runsc`、配额驱动、私网、隔离或卷重连检查失败时，预检返回非零；配置为 `opensandbox` 的 backend readiness 同样返回 503。不得改回 `runc`、local subprocess 或旧 sandbox manager 维持服务。

## 启动、停止与回退

Compose 叠加顺序为基础文件、环境文件、`compose.opensandbox.yaml`。首次只在 Staging 执行，成功的同一组 digest 才能进入生产。

回退程序时先把 backend 工作区 provider 切为 `disabled`，确认没有活动 lease，再停止 OpenSandbox Server。保留 `opensandbox_state` 和所有配额卷；不要执行 `docker compose down -v`，不要删除 volume plugin 数据目录。实例 ID 可以替换，用户 volume ID 必须继续沿用。恢复旧程序后 readiness 只允许 `disabled`，不会退回非隔离执行。
