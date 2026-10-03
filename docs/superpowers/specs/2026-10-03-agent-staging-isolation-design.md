# 新版 PSKit 独立 Staging 环境设计

## 目标与边界

用户希望有一套长期可复用的预发布环境，发布新版时验证前端、Python/Pi、Supabase、LiteLLM 和 AF3 的产品交互，而不反复复制生产用户数据。Staging 与 Production 可运行在同一台阿里云主机，但所有可写状态和密钥隔离。现有 `10.9.8.1:4001` LiteLLM 候选连接生产 Supabase PostgreSQL，只用于本次单库迁移，不能命名为 Staging 或复用于日常发布。

本设计不改变正在运行的旧 PSKit、A6000 后端/AF3 接收器、阿里云生产 Supabase、4000 LiteLLM 和公网 Nginx。仍保留已批准的 SQLite→PostgreSQL **一次性**历史数据迁移；以后发布不再搬运用户数据。首版 Staging 不运行真实 GPU/AF3，也不接真实模型提供商密钥。

## 方案选择

1. **推荐：同一阿里云主机上按需启动完整隔离的 Staging。** 三个独立 Compose 项目包含 Supabase 自有 PostgreSQL、LiteLLM、后端/Pi；同一固定后端镜像和前端 `dist` 可先在 Staging 验收再发布生产。通过 WireGuard 私网访问。资源开销高于单后端测试，因此按需启动，`down` 保留卷。
2. 仅多开一个后端容器、共用生产 Supabase 与 LiteLLM：便宜，但测试写入会污染生产身份、额度或模型账本，不能满足数据隔离。
3. 只在本地运行 mock API：适合快速回归，无法验证阿里云 Docker 网络、Supabase 登录和 LiteLLM 账本。保留现有本地测试，但不把它称为预发布环境。

## 拓扑与命名

```text
WireGuard 10.9.8.1
    └── 私网 Staging 入口（静态 dist + /api/v1 反代）
          ├── pskit-agent-staging: Python/Pi，AF3 mock
          ├── pskit-agent-litellm-staging: LiteLLM + 本地模型替身
          └── pskit-agent-supabase-staging: Auth/Storage/PostgREST + 独立 PG17

公网 agent.bioailab.net
    └── Production：现有独立项目、网络、卷、密钥和真实 A6000 AF3
```

三个 Staging 项目只能加入 `pskit-agent-supabase-staging_default`，不能加入 `pskit-agent-supabase_default`。Supabase 基础 Compose 有 11 个固定 `container_name`，Staging 覆盖文件逐一改名为 `*-staging`；显式命名的数据库卷和 Storage 卷也覆盖为独立名称。其余 Compose 项目卷由不同项目名隔离。项目名、网络名和卷名必须由测试从最终 `docker compose config --format json` 验证。

Staging API、Supabase 网关、LiteLLM 和静态站点仅绑定阿里云 loopback 或 WireGuard 地址，不使用 `0.0.0.0`，也不添加公网 DNS。固定私网端口：Staging Supabase `127.0.0.1:18131`，后端 `127.0.0.1:18090`，LiteLLM `10.9.8.1:4002`，静态站点 `10.9.8.1:18132`；这些端口在设计时尚未被占用。前端仍是 Nginx 提供 `dist`，不新增生产前端容器；Staging 的宿主机 Nginx 配置通过独立 root 脚本安装，仅服务 WireGuard，且不会改写 `agent.bioailab.net.conf`。私网 HTTP 站点的 Staging Cookie 设置 `Secure=false`，传输由 WireGuard 保护；Production 保持 `Secure=true`。浏览器阶段不启用 Google OAuth 或真实邮件发送；测试账号由 Auth 管理接口创建并确认邮箱。

## 配置、密钥与数据

Staging 的 Supabase `.env` 从仓库模板在私有路径生成新的 PostgreSQL 密码、JWT/JWKS、发布密钥和 Storage 密钥；LiteLLM master/salt/虚拟 key、`pskit_app` 数据库密码、AF3 测试回调密钥也重新生成。任何 Staging 文件都不能引用生产 `.env`、生产数据库卷或生产虚拟 key。所有私有文件为 0600，目录为 0700；生成器拒绝覆盖已有文件和符号链接，不把密钥写入日志、参数或 Git。正式生产的“单 PostgreSQL”指生产内部 Supabase/LiteLLM/PSKit 共用一个 PG17；Staging 拥有自己的单独 PG17 实例，不与生产共用角色或数据库。

`staging.sh up|status|logs|down` 是 Staging 的唯一启动入口，明确使用三个 `-p ...-staging` 项目与独立 env/overlay。`up` 依次启动 Supabase、在 Staging PG17 创建 `litellm` 逻辑库及 `pskit` schema、启动 LiteLLM 和后端；`down` 只停 Staging 服务，绝不执行 `down -v`。脚本预检生产/测试 DSN、项目名、卷名、网络名、密钥和端口的差异，并验证镜像 digest 已在本地。它不得触碰现有 `stack.sh` 的生产项目。生产切流量和宿主机 Nginx 的 root 步骤仍是单独流程。

测试数据由幂等 `seed-staging` 命令创建：一个 `@example.invalid` 的已确认测试账号、一个项目、会话、少量文件与配额样本；账号密码仅保存在 0600 Staging 文件。Agent 使用 Pi 与 LiteLLM；LiteLLM 的 `claude-opus-4-8` 测试别名指向隔离的模型替身，并保留 10/2 美元账本契约。AF3 设为 `mock`，所有结果标记 `simulation=true`，不触及 10.9.8.2。不得导入生产 SQLite、Pi transcript、Supabase Auth/Storage 或 LiteLLM key。重置测试数据必须是显式、单独的操作，并在销毁前确认 Staging 项目与卷名；常规发布不需要重置。

## 发布与验收

发布流程固定为：构建一次固定镜像和前端 `dist` → 用相同 digest 启动 Staging → 运行 TDD/契约测试、私网登录/聊天/上传、SSE、Token/GPU 限额、LiteLLM 计量与 mock AF3 验收 → 将同一镜像 digest 和 `dist` 发布到 Production → 运行兼容的数据库**结构**迁移并切服务。若新 schema 不兼容旧程序，先做 expand/contract 迁移，不通过复制生产数据到 Staging 来试错。

验收必须证明：Staging 写入后生产 `auth.users`、`pskit`、`litellm` 行数与账本不变；两个环境的 Docker 网络/卷/密钥均不同；未授权 `/internal/` 仍拒绝；Staging 关闭后生产健康；没有真实 AF3 claim。生产一次性历史迁移仍遵循既有《单 PostgreSQL 切换与回退》手册，并在真实模型提供商和 A6000 门禁就绪后执行。

## 风险与回退

Supabase 有固定容器名和显式卷名，漏覆写会产生冲突或误用生产卷，因此 Compose 静态隔离测试是启动前硬门禁。阿里云当前约 14 GiB 内存，完整 Staging 按需运行；启动前检查剩余内存与磁盘，不满足则保持关闭。Staging 故障只停止 Staging 项目并保留日志与卷；任何脚本若解析出生产项目、网络、卷或 DSN，立即拒绝运行。生产回退流程与 Staging 无关。
