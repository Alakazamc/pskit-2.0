# Tool Product 结果展示与 Agent 草稿交接发布

日期：2026-10-08。发布源码提交：`807ad92f317394015f9c50356df1bfc88ac66500`。功能提交：`079de67`、`e06ed00`、`8c5ce1d`。

## 内容

- Tool Product Run 快照返回本次实际提交的不可变 `arguments`；CORAL 等配置型工具在结果侧展示真实运行参数、完成状态、用量来源、摘要和产物。
- “Continue with Agent / 交给 Agent”先通过受所有权保护的 Artifact API 下载完整产物，再登记成当前用户文件。
- 文件上传完成后创建新会话，将运行参数、结果摘要和文件引用写入该会话的浏览器本地 Composer 草稿并跳转。交接不会自动发送；用户可以检查、编辑和删除附件后手动发送。
- 旧的通用 MCP 工具和 PDB 搜索结果采用相同的“新会话草稿”交接语义。

## 制品

| 项目 | 值 |
| --- | --- |
| 后端镜像 | `pskit-agent-backend:20261008-tool-handoff-807ad92` |
| 阿里云镜像 ID | `sha256:1a4cad73f0ae9b0f60f20de51ab139a02f6aeaa681cfc9fc5f427adea42eb3f3` |
| revision label | `807ad92f317394015f9c50356df1bfc88ac66500` |
| 镜像归档 SHA256 | `026eb182f2ab331a2eca970e4853caec617dbb5488d0c9d8cf2c042d58384194` |
| 源码归档 SHA256 | `475c102a9baadb1f9d398fb0a496c5bee08f124ec4c946dcd92759c63c10d5e9` |
| 补充 infra 归档 SHA256 | `dd489c42a36d80a05bd7356415902d11e1e2d3afc8a0ad2b30f5570d6744c1c2` |
| 前端归档 SHA256 | `ddcb590785c11161a14c821eea0ccccbafe5d8e108c70196177165d11c10f28d` |
| 完整 dist SHA256 | `e9403377d351007cc48d68322006811b0cc1a94a3b95143c5077c3abb12083ab` |
| index SHA256 | `7fde1c37ad3cf4d27c6bd6e4d40919d023e090139672d34073da330f28bd191b` |
| 文件数 | 401 |
| 生产 Turnstile 公钥 SHA256 | `91c177d7f198741dbd53193a4555987bc5ac5c40a4c74cb96101e403aeaa3c1a` |
| 阿里云发布目录 | `/home/ecs-user/pskit-agent-releases/20261008-tool-handoff-807ad92` |

本地后端使用 Dockerfile 锁定的 Node/Python digest 和已验收的本地镜像引用完成构建，绕过阿里云访问 Docker Hub token 接口超时。新旧后端镜像的 `pip freeze` SHA256 均为 `7dc381fe3891a3b1fc556a83a97b632c7390bf13426b712cb9257b36ad6b3aff`。镜像导入、应用导入、revision label 和 `ToolRunSnapshot.arguments` 探针通过。

前端使用生产 Supabase、`/api/v1` 和生产 Turnstile site key 完成 typecheck、Vite build 及 Molstar 分包检查；Molstar 保持 18 个延迟 chunk。没有执行前端测试套件。

## Staging

Staging 保留既有私有配置、账号和卷，更新到同一后端镜像及前端制品。首次从不可变发布目录运行停止脚本时，发现归档缺少 Compose 引用的公开 `infra/` 文件；该次未修改 pin。补传 Git 中的 `infra/` 后成功停止并更新 pin，旧 pin 位于：

`/home/ecs-user/pskit-agent-staging-private/release-backups/pins-i9nzna8g`

启动预检需要部署 checkout 中的生产私有 Supabase 配置做隔离比较，因此最终从既有部署 checkout 启动已经锁定的新制品，没有把私有配置复制进发布目录。

验收结果：Staging backend healthy；私网入口使用的 index 与制品逐字节一致；幂等 seed 成功；完整 smoke 通过登录、文件上传、SSE、Token/GPU 配额、模拟 AF3，并确认生产数据快照不变。只读 CORAL 快照探针发现 Staging 当前没有保留可见的 CORAL 历史 Run，因此没有为了字段验证重新触发模型或 GPU 任务。

## 生产

切换前 `agent_runs=0`、活动 `agent_jobs=0`、`pending_reconciliation=0`。数据库中有两条 2026-10-07 遗留 Tool Product Run 仍标记为 `queued`，但对应 Job 已分别为 `cancelled/settled` 和 `failed/settled`；它们不是活动计算，本次保留原记录，没有修改数据。

停止旧 backend 后再次确认无活动 Run、Job 或待对账用量，只更新 `cloud.env` 的固定后端镜像字段并定向重建 backend。现有 `.env.stack` 只有三个配置文件路径键，不包含镜像 pin；Compose 解析确认 backend 使用新镜像。Supabase、PostgreSQL、LiteLLM、AF3 回调代理、A6000 接收器和计算容器均未重启。

生产静态目录先完整备份，再复制新 assets，最后原子替换 `index.html`。验收结果：readiness 200、登录页 200、未登录 usage 401、internal 404、管理员公网 403、WireGuard 管理入口 200；生产和 Staging HTTP index SHA256 均为 `7fde1c37ad3cf4d27c6bd6e4d40919d023e090139672d34073da330f28bd191b`。切换后活动 Run、Job 与待对账用量仍为 0。

## 回滚

- 生产旧 `cloud.env` 与后端身份记录：发布目录的 `rollback/`。
- Staging 旧 pin：`/home/ecs-user/pskit-agent-staging-private/release-backups/pins-i9nzna8g`。
- 完整 Staging 静态备份：发布目录的 `rollback/staging-static`。
- 完整生产静态备份：发布目录的 `rollback/production-static`。
- 旧生产后端镜像：`pskit-agent-backend:20261008-vision-23f0efc`，ID `sha256:3f7dab0c322545be70ca860499bab8ce4edf223626e15a67b12eb88125560726`。

回滚前再次检查活动 Run、Job 和 schema 兼容性。后端只恢复固定镜像 pin并定向重建；前端从完整静态备份复制资源并原子恢复旧 `index.html`。不恢复数据库，不重启 A6000。
