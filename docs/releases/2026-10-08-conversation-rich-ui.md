# 对话 Rich UI 前端发布

日期：2026-10-08。功能提交：`b4d7c23`；交接提交：`d6ab8ff`。本次仅发布前端静态文件，生产与 Staging 后端镜像、数据库、Supabase、LiteLLM、Nginx 配置和 A6000 接收器均未变更。

## 内容

- 对话消息统一通过 typed message part 注册器渲染，历史消息与实时运行共享工具、进度、错误、引用、资源和 Artifact 组件。
- 文件与 Artifact 使用已有鉴权资源 ID 打开、预览和下载；未知类型保留可读降级，不解析模型文本中的本地文件路径。
- Markdown 文本继续使用现有 Streamdown 渲染；结构化资源显示文件类型、名称、大小及对应操作。
- 前端约定与实现计划同步记录了 typed resource 引用、渲染注册器和安全边界。

## 制品

| 项目 | 值 |
| --- | --- |
| 前端归档 | `/tmp/pskit-rich-ui-b4d7c23-frontend.tar.gz` |
| 前端归档 SHA256 | `027a0c08d0dc3082dbad27035489aa2061b7dc7b274a8afc6ea1534643e40d8a` |
| 完整 dist SHA256 | `bc97a8da678138b0ee7fb8980ac09cc450e2f85d0c25d468d24afcb57a78bad9` |
| index SHA256 | `7153b60bb3dc4b5532d612ba7e2c2f5741e4c61c209f7bc1a7af83f3355cbbb1` |
| 文件数 | 401 |
| 应用主包 | `/assets/new_frontend-DxuF51zD.js`，HTTP 200，742945 bytes |
| 生产 Turnstile 公钥 SHA256 | `91c177d7f198741dbd53193a4555987bc5ac5c40a4c74cb96101e403aeaa3c1a` |
| 阿里云发布目录 | `/home/ecs-user/pskit-agent-releases/20261008-conversation-rich-ui-b4d7c23` |

本地执行 typecheck、lint 和生产构建均通过；Molstar 保持 18 个延迟 chunk。构建使用生产认证模式、`/api/v1` 和服务器现有 Turnstile site key；上传后重新核对归档、dist、index 哈希，并确认构建中包含对应 site key。按当前任务约束没有运行前端自动化测试套件。

## Staging

Staging 保留既有私有配置、账号、卷和后端镜像 `pskit-agent-backend:20261008-tool-handoff-807ad92`，只更新前端制品 pin 和静态目录。旧 pin 备份位于：

`/home/ecs-user/pskit-agent-staging-private/release-backups/pins-_6csbfpi`

首次 seed 紧接容器启动时连接被重置；后端随后进入 healthy，原样重跑 seed 成功，确认是启动时序。首次 smoke 命令还因本地 shell 提前展开远端 Docker socket GID 而无权采集生产只读快照；使用阿里云已核对的 `1000:1000` 和 socket GID `986` 后，私有 API 与 WireGuard Nginx 两层 smoke 均通过：登录、文件上传、Agent SSE、Token/GPU 配额、模拟 AF3，事件数均为 4，生产数据快照不变。

Staging HTTP index SHA256 为 `7153b60bb3dc4b5532d612ba7e2c2f5741e4c61c209f7bc1a7af83f3355cbbb1`，与发布制品一致。

## 生产

生产静态目录先完整备份，再复制新资源，最后原子替换 `index.html`。发布后验证：

- `https://agent.bioailab.net/login` 返回 200；
- 生产与 Staging HTTP index SHA256 均与制品一致；
- 应用主包返回 200；
- 未登录 `/api/v1/usage` 返回 401；
- 公网 `/internal/compute/jobs/claim` 返回 404；
- 公网 `/admin/models` 返回 403；
- 阿里云后端 readiness 返回 200，镜像仍为 `pskit-agent-backend:20261008-tool-handoff-807ad92` 且 healthy。

A6000 唯一接收器仍挂载 `/data/jhli/pskit-mcp-receiver-20261007/runtime-e164dab`，容器启动时间仍为 `2026-10-07T16:50:01.443123546Z`，本次没有重启或修改 GPU 侧服务。

## 回滚

- Staging 完整静态备份：`/home/ecs-user/pskit-agent-releases/20261008-conversation-rich-ui-b4d7c23/rollback-staging.tar.gz`。
- 生产完整静态备份：`/home/ecs-user/pskit-agent-releases/20261008-conversation-rich-ui-b4d7c23/rollback-production.tar.gz`。
- Staging 旧 pin：`/home/ecs-user/pskit-agent-staging-private/release-backups/pins-_6csbfpi`。
- 发布前生产与 Staging index SHA256：`7fde1c37ad3cf4d27c6bd6e4d40919d023e090139672d34073da330f28bd191b`。

前端回滚时解包对应静态备份，先恢复资源文件，最后原子恢复旧 `index.html`；无需回滚数据库、后端镜像或 A6000。
