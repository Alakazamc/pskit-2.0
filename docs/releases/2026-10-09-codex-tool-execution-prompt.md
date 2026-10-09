# Codex 式工具执行提示词发布

日期：2026-10-09（Asia/Shanghai）

## 发布内容

提交 `2d21370` 扩展 Pi system prompt 的执行规范：用户要求实际操作时，优先检查本 Run 注册的工具并执行；修改前读取目标；执行后检查工具结果；失败或部分完成时只报告已证实的状态。明确禁止把 XML、伪工具标记、JSON 或代码块作为工具调用。

文件产物规则现在区分“写入成功”和“客户端附件卡片已注册”：正式文件写入当前 Session attempt 的 `artifacts/<attempt_id>/`，系统在 Run 收尾时收集并发布产物事件；助手不得凭写入成功伪称附件卡片已生成。

这采用 Codex 可观察到的工具执行与结果核验模式，没有复制任何产品内部提示词。

## Staging 与生产验收

- Staging 使用合成账号通过登录、文件上传、Agent SSE、配额和模拟 AF3 smoke（4 个 SSE 事件）；生产只读数据库快照保持一致。
- 原 Staging 合成账号的 20,000 Token 额度已耗尽，第一次 Agent smoke 在模型调用前返回 `TOKEN_QUOTA_EXCEEDED`。没有删除或重置旧账号及数据；仅将 Staging 私有 `seed.env` 切到新合成邮箱，并备份到 `/home/ecs-user/pskit-agent-staging-private/release-backups/seed.env.pre-2d21370` 后重跑通过。
- Staging 与生产后端 readiness 均为 200。生产公网 `/login` 返回 200、未登录 `/api/v1/usage` 返回 401、`/internal/` 返回 404；Run、Job 与 Workspace Attempt 均无非终态记录。
- 两个 backend 容器 `/app/pi/system-prompt.md` 的 SHA256 均为 `fc2319a50dd95b84e75c5dec94e66e561aded92ad86d01db65f22208022ecfa4`，与源码一致。
- 没有调用真实付费模型；Staging 模型使用替身。因此仍需在普通生产对话中确认真实模型实际调用 `write`，并确认产物事件在对话右上角“产物”面板出现。

## 制品与回滚

- 源码提交：`2d21370`（`codex/new-stack-baseline`）。
- 镜像：`pskit-agent-backend:20261009-2d21370-prompt`，image ID `sha256:76dadc6a5baf9c082e83da426f522b907f74914928857c2bffe5c267de8f5271`。
- 镜像仅以之前已发布的 `sha256:447b72230a23e73d4812c1ad1e7183f318771867ad7ad5ddc50c17042c7e7cab` 为基底，覆盖 `/app/pi/system-prompt.md`。构建没有拉取基础镜像，也未改变依赖、schema、数据卷或 Compose 拓扑。
- 生产 backend 已更新；Supabase/PostgreSQL、LiteLLM、AF3 callback proxy、OpenSandbox、前端与 A6000 接收器未重启或改动。前端静态文件未变化。
- 生产配置备份：`/home/ecs-user/pskit-agent-releases/20261009-2d21370-prompt/production-backup/cloud.env.pre-2d21370`（模式 0600）。生产当前 backend 旧镜像为 `pskit-agent-backend:20261009-230b94e-prompt`。
- 回滚：将上述备份原子恢复为生产 `deploy/agent/cloud.env`，然后在部署目录使用既有 Compose 文件执行 `docker compose --env-file cloud.env -f compose.yaml -f compose.cloud.yaml -f compose.postgres.yaml -f compose.opensandbox.yaml -f compose.opensandbox.production.yaml -p pskit-agent-cloud up -d --no-deps --wait backend`。数据库及其他服务不需要回滚。
- Staging pins 备份：`/home/ecs-user/pskit-agent-staging-private/release-backups/pins-hotw7b_q`。回退 Staging 时先按 `deploy/agent/staging.sh down` 停止其专属项目，再恢复 pins 并启动。
