# 流式性能与后端质量发布

日期：2026-10-07。生产入口：<https://agent.bioailab.net>。固定源码：`a28b9af069cfcccbd42e1d3f009abc9b2c7a1abe`。

## 发布内容

- 发布 `9d0d422`、`dee2e64` 的前端流式更新，缩短首段显示等待并减少增量渲染开销。
- 发布 `f215a12` 的后端错误码、结构化日志、重试与数据库工具整理。
- `a28b9af` 恢复远程 MCP 失败但缺少资源用量时的安全边界：保留配额占用并等待对账，不补造零用量，也不把提供方原始错误写入日志。
- Vitest 显式清空本地生产 Turnstile site key；需要验证码的测试自行注入 key，避免 `.env.local` 污染认证测试。

本次使用已验收的制品链路：本地构建与测试，Windows OpenSSH/SCP 上传，独立 Staging 验收，再定向更新生产 backend 和静态文件。没有运行会 `git pull`、全栈 `down/up` 或 `rsync --delete` 的通用部署脚本。

## 制品身份

| 项目 | 值 |
| --- | --- |
| 阿里云发布目录 | `/home/ecs-user/pskit-agent-releases/20261007-streaming-a28b9af` |
| 前端完整 dist SHA256 | `f4eb708783ed2b95f1286fbc2b554ca19d04317bb36ed1b95109e296e01f85ba` |
| 前端 index SHA256 | `69df9f3be6ba6028073f00aeb002265de67ac9f22afb8319a2b30a5218a3a166` |
| 前端归档 SHA256 | `19c4d3ff54786d800c5126c3f97d0b878ae11012604021f8f17b6a4ab98d4b7c` |
| 源码归档 SHA256 | `9a53e649173851e3b25dd3f015096a5438825e636b6d7176b4e03a124fb4615a` |
| 后端镜像 | `pskit-agent-backend:20261007-streaming-a28b9af` |
| 阿里云运行镜像 ID | `sha256:d713d9b57327ac231462c8b229cfaebb35178ba88413145409b8cebc3cc73e5f` |
| 本地 OCI index ID | `sha256:76a22075233bf02f8507210fa0405777f35cc5c419eba02e8678a424e999231a` |
| 后端 gzip 镜像包 SHA256 | `442092b1fd5dd754914a766f304dd3597c97f00f8100bcd9faedbec9b3f2d85d` |

前端使用 Supabase 认证、`/api/v1` 和既有生产 Turnstile 公共 site key 构建。后端基础镜像仍是 Dockerfile 中固定的 Node/Python digest；Docker Hub 元数据在 WSL NAT 下超时，因此从本地回环镜像缓存读取相同 digest。默认 PyPI 出口只有约 20 KB/s，临时构建文件改用清华 PyPI 镜像后约 8–12 MB/s。新旧后端镜像的 `pip freeze` 完全一致；npm 使用原锁文件。该临时镜像地址没有写入产品 Dockerfile。

## 验证结果

| 检查 | 结果与边界 |
| --- | --- |
| 前端 | typecheck、lint、46 个测试文件、284 项测试通过；生产构建和 Molstar 18 chunk 校验通过 |
| 后端静态检查 | Ruff 全部通过；MCP 对账回归与 CORAL 协议测试 7 项通过 |
| 后端完整 suite | 577 passed、281 skipped；一个既有 MCP 租约时序用例在整套高负载下失败，隔离连续 5 次通过，记录为时序抖动而非宣称全绿 |
| 镜像 | 新镜像导入 smoke 通过；完整 commit label 正确；新旧 Python 依赖版本逐项相同 |
| Staging | 私有 API 与 WireGuard Nginx 各跑一次 smoke；登录、文件、SSE、配额、模拟 AF3 均通过，每次收到 4 个事件，生产只读快照未变化 |
| 静态文件 | Staging 与生产各 401 个新文件逐一匹配制品；先复制资源，最后原子替换 index，旧 hash assets 保留 |
| 生产 | backend healthy；`/login` 200、未登录 usage 401、`/internal/` 404；index 引用的 4 个首屏资源均可读取；未完成 Agent Run 为 0 |

没有进行真实付费模型、CORAL 或 AF3 推理。本次没有 schema 迁移，没有重启 PostgreSQL、Supabase、LiteLLM、Nginx 或 AF3 计算容器。

## 已知未完成项

A6000 唯一接收器仍挂载 `runtime-c1d7407`。发布前发现旧 CORAL Job `compute-700e7c142f0747268eb8cf40d9960696` 已在 journal 的 `executing` 状态约 9 小时，而中央状态为 `cancelling`、`pending_reconciliation`；另一个 CORAL Job 处于 `queued`。接收器在任务开始后重启过，当前协议没有可信的 detached recover，因此本次没有删除 journal、补造用量、重提任务或替换接收器 runtime。需要管理员结合提供方记录完成用量对账和终态处理后，再更新 A6000 接收器。

## 回滚

回滚只恢复程序和页面入口，保留数据库、工作区与计算 journal。

1. 生产前端完整旧目录位于发布目录 `rollback/frontend`；恢复时仍应先复制资源、最后原子替换 index。
2. 原生产 `cloud.env` 位于 `rollback/cloud.env`，原镜像为 `pskit-agent-backend:20261007-message-images-c1d7407`，配置 ID `sha256:04f6332abffa91792b082bd38096234270e637a07535d11206fbdc459bcb825a`。核对发布后的配置变化后，只恢复 `AGENT_BACKEND_IMAGE` 并定向更新 backend。
3. Staging 原 pin 备份为 `/home/ecs-user/pskit-agent-staging-private/release-backups/pins-79securp`；测试账号、卷和密钥保持不变。
4. A6000 本次未切换，不需要回滚。处理旧 CORAL 记录时不得删除未确认用量或覆盖 journal。

