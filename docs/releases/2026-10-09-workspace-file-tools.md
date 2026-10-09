# Workspace 文件写入与产物下载修复

日期：2026-10-09（Asia/Shanghai）

## 发布内容

提交 `230b94e` 修复了 Agent 把 `<write_file>` 伪指令当普通文本输出、却声称文件已创建的问题。Pi 实际注册的 workspace 工具名是 `write`，参数为 `path` 和 `content`。系统提示现在要求助手调用真实 `write` 工具，将文件写入当前 attempt 的 `artifacts/<attempt_id>/`，只有工具成功后才能报告已创建；写入失败时必须如实说明，并引导用户从对话右上角“产物”面板预览或下载。提示词禁止伪造工具标记和 `sandbox:/mnt/data/...` 链接。

## 验证

- 本地回归：workspace 提示约束、产物下载 API、会话产物隔离共 `3 passed`；Pi extension Node 测试通过；`git diff --check` 通过。
- 云端 Staging 使用合成用户通过登录、文件上传、Agent SSE、Token/GPU 配额和模拟 AF3 smoke（4 个 SSE 事件）；生产数据库前后只读快照一致。
- Staging 与生产 readiness 均返回 200；生产公网登录页返回 200，未登录 usage API 返回 401。
- Staging 和生产运行容器中的 `/app/pi/system-prompt.md` SHA256 均为 `3a093d3cb72d5839723736aa8d0ceb86bb8746e5b3a1f547e314de2a5bef3fd7`，与提交源码一致。
- Staging smoke 使用模型替身，没有调用真实付费模型；本次未从真实聊天端到端触发模型的文件写入。

## 制品与发布路径

- 源码提交：`230b94e`（分支 `codex/new-stack-baseline`）。
- 阿里云镜像：`pskit-agent-backend:20261009-230b94e-prompt`，image ID `sha256:447b72230a23e73d4812c1ad1e7183f318771867ad7ad5ddc50c17042c7e7cab`。
- 由于阿里云访问 Docker Hub 的固定 Node/Python 基础镜像时超时，常规全量 Docker build 未能运行。该制品以生产原镜像 `sha256:6c8cfe2edf465b003eec11baa7a49013a75ea1255e9a54ae01c65f5e877bcbb3` 为精确基底，只覆盖本次唯一变更的系统提示词文件；未改 Python/Node 依赖、数据库、OpenSandbox、workspace 或 AF3 计算镜像。
- 生产 `cloud.env` 回滚副本：`/home/ecs-user/pskit-agent-releases/20261009-230b94e/production-backup/cloud.env.pre-230b94e-153043`。恢复该文件后，以原 Compose 文件和 `pskit-agent-cloud` 项目运行 `up -d --wait backend af3-callback-proxy` 可恢复旧后端及回调代理镜像。两版镜像均保留在阿里云，未清理。
- Staging 旧 pins 备份：`/home/ecs-user/pskit-agent-staging-private/release-backups/pins-zp4tz5bs`。数据库卷、生产数据、LiteLLM、OpenSandbox Server、A6000 接收器和前端静态文件均未替换或删除。因 `AGENT_BACKEND_IMAGE` 同时供生产 backend 与 AF3 callback proxy 使用，两个运行服务都更新到了新镜像；无活动 AF3/Agent/Workspace 任务。
