# 用户沙箱与 Pi RPC

`compose.sandbox.yaml` 是可选叠加层。Python API 仍负责身份、会话、Token/GPU 配额、Run、MCP 和 AF3；Pi RPC 进程在用户专属容器内启动。工具集页面的“交给 Agent 分析”通过普通会话消息接口创建 Run，因此也使用相同的配额和审计链路。直接运行 MCP 工具仍由原来的 Python 能力接口处理。

## 布局与生命周期

- 每个用户一个 `pskit-sbx-<namespace>-<hash>` 容器和一个同名 `-workspace` Docker 卷；会话目录是卷内 `/workspace/sessions/<session_id>/`。Pi 在该目录作为当前工作目录运行，会话 transcript 在成功后由 Python 记录路径。
- API 只持有沙箱管理器的私有令牌；只有管理器挂载 Docker socket。沙箱没有 Docker socket、宿主端口或特权能力，使用 UID 10001、只读根文件系统、1 GiB 内存、1 CPU、256 PIDs，只接入 Compose 的私有 `app` 网络。管理器只提供创建/唤醒与空闲停止接口。
- 30 分钟没有新 Pi 请求的容器会停止；卷不删除，下一次请求复用。管理器重启时会重新发现本环境的运行容器。首次从旧后端切换时，已有 Pi transcript 在下一轮按会话导入沙箱，成功后数据库中的路径更新。
- 当前 Pi 仍关闭内置 shell、读写和外部 Skills 工具。已上传文件由 Python 作为受限上下文传给 Pi；文件正文尚未镜像到沙箱目录。不能把“有目录”理解为已经开放任意代码执行或已实现文件双向同步。

## 启用

先构建或载入与后端**相同代码版本**的固定标签镜像。标签不可复用；生产和 Staging 使用不同 namespace、Compose 网络、数据卷和管理令牌。管理器必须在对应 Docker 主机上运行，不能把 Docker socket 暴露给 Web 或公开网络。

在部署目录的 `.env`（权限 `0600`）设置：

```dotenv
AGENT_SANDBOX_IMAGE=pskit-agent-backend:your-immutable-release-tag
AGENT_SANDBOX_NETWORK=pskit-agent-cloud_app
AGENT_SANDBOX_NAMESPACE=cloud
AGENT_SANDBOX_MANAGER_TOKEN=<独立随机密钥，至少 16 字符>
AGENT_SANDBOX_BRIDGE_SECRET=<另一枚独立随机密钥，至少 16 字符>
```

`AGENT_SANDBOX_IMAGE` 必须对应当前 `AGENT_BACKEND_IMAGE` 的内容。`AGENT_SANDBOX_NETWORK` 应以 `docker network ls` 核对实际 Compose `app` 网络名称；Staging 通常是 `pskit-agent-staging_app`。不要将管理令牌或 bridge secret 放入前端构建变量。模型别名从 LiteLLM 动态读取；如果 LiteLLM 未返回 `supports_vision`，可在后端环境设置 `MODEL_GATEWAY_IMAGE_MODELS_JSON=["模型别名"]`，仅为已验证支持图片的别名开启图片上传。

生产 Compose 在原有四个文件后叠加沙箱文件；第一次切换前先执行配置检查，确认 `sandbox-manager` 没有 `ports`，且只有它挂载 Docker socket：

```bash
docker compose --env-file .env \
  -f compose.yaml -f compose.cloud.yaml -f compose.postgres.yaml -f compose.sandbox.yaml \
  config --quiet
docker compose --env-file .env \
  -f compose.yaml -f compose.cloud.yaml -f compose.postgres.yaml -f compose.sandbox.yaml \
  up -d sandbox-manager backend
```

Staging 在它原有的 Compose 文件组合末尾叠加同一个 `compose.sandbox.yaml`，使用独立的 Staging 变量。先在 Staging 用一名测试用户发送消息，确认管理器仅生成一个容器、第二个会话创建另一目录、重启容器后 transcript 保留，再切生产。后端 `GET /health/ready` 仍负责数据库就绪；沙箱容器按需创建，不要求每名用户预热。

回退时从 Compose 命令中移除 `compose.sandbox.yaml` 并重建 backend，恢复本地 Pi 模式。**新产生的沙箱 transcript 路径无法由旧模式直接读取**；回退前应暂停新 Run，导出/转换这些 transcript，或保留沙箱服务直到在沙箱内完成未结束的会话。不要运行 `down -v`，否则会删除工作区数据。

Docker socket 使管理器具有宿主 Docker 控制权，因此它是受信任的基础设施组件。用户沙箱只拥有私有应用网络和自身工作卷；如果未来开放 shell、代码执行或不可信 Skill，需要单独审查网络出口、文件同步和更强的容器隔离。
