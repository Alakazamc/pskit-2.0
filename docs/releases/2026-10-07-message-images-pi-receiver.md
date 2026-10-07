# 图片消息、Pi 大图片与接收器诊断发布

日期：2026-10-07。生产入口：<https://agent.bioailab.net>。固定源码：`c1d7407570d92778f4618c182b3257e65dee2fd3`。

## 提交与发布内容

- `5684021`：已发送图片显示在用户文字气泡上方；输入区与消息共用鉴权图片预览缓存，切换会话和刷新可按文件 ID 恢复，下载失败可重试。
- `d4b342b`：模型失败响应缺少 usage 时输出安全错误码 `MCP_FAILED_USAGE_MISSING`，保留 journal 和待核对用量，不补造零耗时。
- `c1d7407`：记录本地前后端开发、SSH 连接与云端发布背景。
- 同时部署此前 `e3e9041` 的 Pi RPC 64 MiB 行读取上限与接收器安全诊断；支持图片 JSONL 的读取边界修复，未改变单文件和每轮上传额度。

本次先在独立 Staging 验收，再定向发布生产。前端 dist 和完整后端镜像包通过 Windows SCP 上传并核对 SHA256；A6000 使用已提交源码的只读 runtime 快照。没有复制测试数据库、执行 schema 迁移、修改 Nginx 或重启生产 Supabase/LiteLLM，也未操作其他人创建的旧 PSKit 容器。

发布期间新增的 `f215a12` 后端质量改进和 `9d0d422` 前端流式性能提交不属于本次制品；它们需要独立验证和发布。

## 制品身份

| 项目 | 值 |
| --- | --- |
| 阿里云发布目录 | `/home/ecs-user/pskit-agent-releases/20261007-message-images-c1d7407` |
| 完整 dist SHA256 | `dd68415790742b00e9ad5a10da126072a6b0d2d5e6ecbb0daf446ad4b9f5f7a7` |
| index SHA256 | `d0f6ab906de2cbf5f31c0f0f5775c9fe5a2138a955cfc8c070deb928fe002b57` |
| 源码归档 SHA256 | `7904a9e5b3846acd7a2292a85c33f019160056ed2938e1bf2b4474ff507ff91f` |
| 源码/dist 发布包 SHA256 | `322ecf66ef042e9801e95612aa0417bf1824618d82f2b6d56f8cb0d8c7c5ee0b` |
| 后端镜像 | `pskit-agent-backend:20261007-message-images-c1d7407` |
| 后端配置/运行镜像 ID | `sha256:04f6332abffa91792b082bd38096234270e637a07535d11206fbdc459bcb825a` |
| 本地 OCI index ID | `sha256:adbb7ad2f0b181bb1aff12b2d6f4f93d7978e0d266c6d4c37fff6ee9d5342a4e` |
| 后端 gzip 镜像包 SHA256 | `441f390bdfb59521bedb51595779929cb05e71bbdb04a8a6258a4543315d5ea7` |
| A6000 runtime | `/data/jhli/pskit-mcp-receiver-20261007/runtime-c1d7407` |
| 接收器源码包 SHA256 | `b2a886de0b2889579f011067131cb91887858b5250b40f70b435dbb53a20efcd` |
| 保留的接收器镜像 ID | `sha256:67d5aa962921e08be408ebce0852bca6e86afaabbb39291634dcbb5bb59dcece` |

前端构建使用 Supabase 认证、`/api/v1` 和既有生产 Turnstile 公共 site key。服务端密钥不在制品或文档中。

本地 Docker 的 containerd 存储返回 OCI index ID，阿里云经典存储返回平台配置 ID。通过读取镜像归档的 manifest、计算配置 blob 哈希、核对源码 label 与归档 SHA256，确认两端来自同一镜像包；并未因 ID 表示差异重新构建。

## 实际部署结果

两环境原后端 `20261007-downloads-af3-tools` 已替换为上述同一镜像；原镜像 ID 为 `sha256:d3a04483ccb92ae8b477e57e5e850061bcf598d355e3c6e2533e5c9614fd343e`。生产前端此前为 `f2c497f`。

- `pskit-agent-cloud-backend-1`、`pskit-agent-staging-backend-1` 均 healthy，运行 ID 与源码 label 符合制品。
- 生产 ready 为 `ready / live / pi / callback`；完整运行环境按键值核对一致，挂载来源和目的地一致。
- 每个静态目录的 401 个新文件与制品逐一相同；HTTPS index 和入口资源逐字节核对。先复制 assets，最后原子替换 index，保留旧 assets。
- 云端公开源码同步 21 个文件；复制前核对旧内容哈希，原文件保留在发布目录的 `rollback/public-source`。
- A6000 唯一 `pskit-mcp-receiver-a6000` 使用新 runtime，RestartCount 为 0；依赖镜像、环境键值和持久挂载保持原值。计算容器未停止。
- 升级前后 CORAL journal 均为一条 executing，两份 AF3 journal 均为空，三个 spool 目录保留。未确认结果未被删除或重复提交。

## 验证范围

| 检查 | 结果与边界 |
| --- | --- |
| 前端 | typecheck、lint、284 项测试通过；正式构建成功 |
| Molstar | 18 个延迟 chunk；最大 436,643 bytes；未进入首屏依赖 |
| 本地图片 UI | 八组通过：中英文 × 明暗主题 × 桌面/手机；覆盖图片位置、失败重试、十张滚动区和键盘操作 |
| 本地前后端 | mock API 登录、上传、鉴权下载通过 |
| 后端完整 suite | 最终运行 572 passed、281 skipped、1 warning；独立 PostgreSQL/可选环境用例跳过，不能据此声称通过 |
| Pi 网关 | 十项通过，含图片边界、SSE、工具、恢复与限流；供应商使用本地替身，没有真实计费 |
| Staging 实际 API | 经现有私网 Nginx：合成账号登录、文件、SSE、配额、AF3 替身通过；四个事件，`simulation=true` |
| Staging 隔离 | 烟测前后生产只读数据库/网关快照一致；烟测容器没有 Docker socket |
| 生产图片 UI | 八组通过；静态资源来自真实 HTTPS，业务 API 使用合成夹具，不等于生产账号或真实模型验收 |
| 生产 HTTP | `/login` 200、未登录 usage 401、internal 404、管理页及管理 API 403 |
| 匿名会话恢复 | 无 refresh Cookie 时 CSRF 为 204，refresh 为 401；服务器内探针后者约 8 ms，不代表用户公网网络延迟 |
| AF3 MCP 隔离 | 无 Bearer 凭据访问返回 404，符合鉴权包装器设计 |

没有真实 CORAL/AF3 推理，也没有付费 LLM 请求。本次保留的旧 CORAL 不确定任务仍需提供方返回及计量记录核对，安全诊断改进不等于该任务已完成。

## 发布期间的问题

- 阿里云与本地 Docker 直连 Docker Hub 均超时。使用本地临时回环转发和显式构建代理，按原 Dockerfile、固定基础镜像 digest、锁文件完整构建一次，然后 SCP 镜像包；没有从旧应用镜像仅 COPY 代码，也没有修改服务器全局网络配置。
- 本地 Snap Docker 包装器影响三个 Compose 测试，改用原生 CLI 与临时插件配置后验证。完整 suite 的 Pi 预算用例曾间歇失败；该模块两次单独通过，最后完整运行通过，保留日志供后续排查。
- 临时发布脚本一次替换误伤 `AGENT_BACKEND_IMAGE` 字段名，另一次把 Env 数组顺序当作配置变化。两次均自动恢复原运行版本；修正为完整标识符替换和环境键值比较后发布成功。
- 最终探针最初错误预期匿名 CSRF 有 JSON、MCP 拒绝码为 401。对照源码更正为 204 和 404 后通过；服务代码未因此修改。

云端发布目录保存 `manifest.json`、`staging-static.json`、`production-static.json`、`source-synced.json`、`staging-api-smoke.json`、`staging-production-snapshots.json`、`production-backend.json` 和 `verification.json`。A6000 记录为 `/data/jhli/pskit-mcp-receiver-20261007/release-c1d7407.json`。

本地证据：`/tmp/pskit-message-release-tests.json`、`/tmp/pskit-message-release-build.log`、`/tmp/pskit-message-release-backend-diagnostics.log`；八组本地截图在 `/tmp/pskit-message-release-local`，生产静态截图在 `/tmp/pskit-message-release-production-images`。

## 撤回方法

撤回是恢复上一版程序和页面入口，保留当前用户数据。先确认活动 Run/Job 与 schema/transcript 兼容，再按部署手册执行。

1. 前端：使用发布目录 `rollback/production-static/index.html`，写入静态 root 的临时文件、设为 0644 后原子替换 index。旧 hash assets 已保留。
2. 生产 backend：本次原 `cloud.env`、`.env.stack` 保留于发布目录 `rollback`，权限 0600；核对其后续是否有新配置修改，再只恢复镜像 pin。使用现有三份 Compose 叠加和 `pskit-agent-cloud` 项目定向 `up -d --no-deps --wait backend`。不覆盖数据库。
3. A6000：旧 runtime 为 `runtime-ed9da4f`，旧 compose pin 与一致性 SQLite 备份在 `/data/jhli/pskit-mcp-receiver-20261007/rollback-c1d7407`。核对未确认记录后只恢复 runtime pin 并更新唯一接收器，保留当前 journal/spool 和模型计算容器；不要用旧 journal 覆盖升级后的写入。
4. Staging：使用本次 `rollback/staging-static` 和私有配置的 pin 备份，保留测试账号和卷。
