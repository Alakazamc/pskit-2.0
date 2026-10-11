# PSKit 当前部署矩阵

## 当前补充：2026-10-12

核对基线 `c37ca61`，新版路径迁移已在 `46c1dc4` 阶段完成，现行代码为 `apps/api/`、`apps/web/`。本节来自当前 YAML、`stack.sh`、Staging render 和部署手册，未 SSH 或读取私有配置；`active-*` 只表示现行引用，不能作为在线服务或宿主资格证明。下方原 Phase 0 表格保留当时事实。

### 当前项目与组合

所有路径相对于仓库；叠加顺序不可交换。Supabase、LiteLLM、Agent 继续是不同 Compose 项目，共享 PostgreSQL 宿主而使用不同逻辑数据库和账号。

| 环境/机器 | 项目与顺序 | 配置和恢复入口 |
| --- | --- | --- |
| 阿里云生产 API | `pskit-agent-cloud`：agent `compose.yaml → compose.cloud.yaml → compose.postgres.yaml` | `cloud.env` 选择固定制品与卷，`cloud.backend.env` 为私有服务配置；`pskitctl --env prod --config …` 按 Manifest 更新/恢复应用 |
| 阿里云 Staging API | `pskit-agent-staging`：上述三份 → `compose.staging.yaml` | 独立 Staging 私有 env、卷和网络；同一 CLI 选择 `--env staging`，不复用生产数据 |
| 生产/Staging OpenSandbox | 对应 API 组合 → `compose.opensandbox.yaml` → `compose.opensandbox.production.yaml` 或 `.staging.yaml` | 专用 server 配置、state/runtime 网络、rollout 目录；executor 专用发布/回退脚本，API 只使用已资格的适配器 |
| 生产 Supabase | `pskit-agent-supabase`：`infra/supabase/docker-compose.yml → compose.cloud.yaml` | 私有 `.env`；bootstrap 独立管理，日常 app release 不 provision/migrate/down |
| Staging Supabase | `pskit-agent-supabase-staging`：上述两份 → `compose.staging.yaml` | 独立 `.env`、数据库、Storage、网络和账号；按 Staging 手册恢复 |
| 生产 LiteLLM | `pskit-agent-litellm`：`infra/litellm/compose.shared-postgres.yaml` | 私有 `.env.shared`；独立逻辑库，固定 `v1.100.3`，既有兼容模块保留；app release 不重启或改模型配置 |
| Staging LiteLLM | `pskit-agent-litellm-staging`：shared-postgres → `compose.staging.yaml` | 独立配置与模型替身；真实供应商另行授权 |
| 阿里云计算入口 | `pskit-agent-compute-ingress`：`compose.compute-ingress.yaml` | 固定 Caddy 配置与已有 API 网络；按 DEPLOYMENT 计算入口章节回退 |
| A6000 | `pskit-agent-a6000-mcp`：`compose.a6000-mcp-receiver.yaml` | 唯一 MCP/AF3 接收器，host network，私有 env 和持久 journal/spool；回退前核对 active/unknown/outbox，不双启 reader |
| 阿里云 Web | 宿主 Nginx，无前端 Compose | dist `/var/www/agent.bioailab.net` 或 `/var/www/agent-staging`；Manifest 原子 index 切换，保留旧 hashed assets；回退不重启数据库 |
| 本地 | `pskit-agent-local`：`compose.yaml → compose.local.yaml` | 独立开发 PostgreSQL/Supabase 与模型替身；9 组合成 render 不等于已运行实机 smoke |

### 17 份 agent Compose 的完整用途

以下均位于 `deploy/agent/`。env 只写变量或文件用途，不包含私有值。`AGENT_PG_DATA_VOLUME` 对应应用工作区数据卷，数据库仍在 Supabase 项目内；不能把 YAML 名称误读为新建 PostgreSQL 服务。

| 文件/分类 | 机器、项目与前置叠加 | env / Source bind | 卷、网络与回退 |
| --- | --- | --- | --- |
| `compose.yaml` — active-prod/staging/local | 各 Agent 项目的基础 | `AGENT_BACKEND_ENV_FILE`、`AGENT_AF3_PROXY_KEY_FILE`；私有 callback key 只读挂载 | `agent_data`；app + 外部 Supabase/callback 网络；保留基础及 env，应用按 Manifest 回退 |
| `compose.cloud.yaml` — active-prod/staging | 阿里云；基础 → cloud | backend/proxy 私有 env；`AGENT_AF3_PROXY_SCRIPT` 只读源码 bind；接收器可选 `AGENT_MCP_RECEIVER_ENV_FILE` | `mcp_receiver_data`、app/mcp_egress；回退保留接收器 journal 和原 source bind |
| `compose.postgres.yaml` — active-prod/staging | 阿里云；基础 → cloud → postgres | `AGENT_PG_DATA_VOLUME` 必填；仅接入已有 PG，web 为 `legacy-web` profile | 显式应用数据卷；Supabase 网络继承；日常不重新导入历史数据 |
| `compose.staging.yaml` — active-staging | 阿里云；上述三份 → staging | 独立 Staging env、`SUPABASE_DOCKER_NETWORK`；禁用生产 callback profile，启用合成 receiver | 独立 PG-data/MCP 卷及 Supabase 网络；Staging Manifest 恢复 |
| `compose.local.yaml` — active-local | 开发机；基础 → local | 私有 local backend/proxy env；mock gateway 脚本只读 bind | 本地专用 agent_data 与 Supabase 网络；按同组合 down 保留卷，不能用于生产 |
| `compose.opensandbox.yaml` — active-prod/staging | 阿里云；对应 API 组合 → opensandbox | `OPENSANDBOX_CONFIG_FILE` 只读；server 挂 Docker socket，backend 不挂 | `OPENSANDBOX_STATE_VOLUME`；app + 独立 internal runtime 网络；executor 专用回退，保留工作区 |
| `compose.opensandbox.production.yaml` — active-prod | production API + opensandbox → production | `OPENSANDBOX_ROLLOUT_DIR` 只读策略 bind | 生产 namespace/state；恢复旧合格 release，不能降级隔离策略来绕过门禁 |
| `compose.opensandbox.staging.yaml` — active-staging | staging API + opensandbox → staging | Staging 专用 rollout 目录只读 bind | 独立 namespace/state；按 Staging executor 手册恢复 |
| `compose.compute-ingress.yaml` — active-prod | 阿里云；独立 compute-ingress 项目 | `compute-ingress.Caddyfile` 只读 bind | 外部 `AGENT_COMPUTE_APP_NETWORK` + ingress；保留旧 Caddy 制品及网络回退 |
| `compose.a6000-mcp-receiver.yaml` — active-prod | A6000；独立统一接收器项目 | `AGENT_MCP_RECEIVER_ENV_FILE`；runtime 只读 bind | host network；journal 与 AF3 spool 为宿主持久 bind；receiver 回退保留未确认记录 |
| `compose.a6000-receiver.yaml` — rollback-only | A6000；历史 AF3-only，`cutover` profile | 历史 receiver env 与脚本只读 bind | host network/原 spool；仅恢复对应历史拓扑，先停止统一 reader 并核对中央账本 |
| `compose.release-registry.yaml` — active-local（可选发布基础设施） | 发布机；独立 registry 项目 | 显式 CLI 项目/registry 配置，无应用凭据 bind | 外部 `pskit-opensandbox-release-registry` 卷；保留镜像及卷，不因非常驻就删除 |
| `compose.a6000.yaml` — migration-only | 历史 A6000 API 项目 | `a6000.backend.env`、旧 api Nginx 配置和 callback key bind | 旧 agent_data/app、host network 代理；仅查对应 A6000_MIGRATION 记录，不是日常 API 入口 |
| `compose.frontend-cloud.yaml` — migration-only | 历史 `pskit-agent-cloud` Web 容器 | 旧 Web 镜像/配置 | 外部 Supabase 网络；历史切换记录恢复，现行 Web 为宿主 dist |
| `compose.cloud-return.yaml` — migration-only | 历史阿里云回迁叠加，无新增服务 | 历史切换变量 | 固定 `pskit-agent-cloud-return-20261003_agent_data`；保留数据，日常 release 不使用 |
| `compose.supabase-relay.yaml` — obsolete-candidate | 历史阿里云 relay 独立项目 | `host-nginx-supabase-private.conf` 只读 bind | host network，无业务账本卷；源码不能证明宿主已删除，核对引用后再单独清理 |
| `compose.sandbox.yaml` — rollback-only/obsolete-candidate | 历史 sandbox-manager 叠加 | 退役 manager 的 Docker socket 与 gateway 配置 bind | app/sandbox/Supabase 网络；不与 OpenSandbox 并行启用，历史恢复需独立审查 |

宿主 runtime 注册、quota-volume 插件、Nginx 首次安装和准入组件首次安装归 **bootstrap-only**，由各安装脚本管理；不是以上现行 Compose 的日常 release 步骤。测试目录中的 Compose 是合成 fixture，不计入这 17 份。旧 `infra/litellm/compose.yaml` 独立数据库为 obsolete-candidate，不取代当前 shared-postgres。

### 验证与尚未授权的门禁

`scripts/ci_render_compose.py` 验证 9 种当前组合，CI 镜像检查使用 `apps/api` build context；控制器拆分不移动 YAML、修改 overlay 顺序、source bind 或卷。线上 source-bind 核对、当前宿主 adoption、真实 Web/API/中断/长任务/回滚与 RPO/RTO 演练需独立授权。本轮不将合成 CI 记作这些门禁已通过。

## 历史 Phase 0 矩阵（2026-10-10，5d70395）

基线5d70395，2026-10-10。以下由源码与已有发布记录导出，未执行今日云端inspect；`active-*` 表示现行手册/启动代码引用，不能当作在线容器存活证明。没有删除配置。

| 角色/环境 | 项目及叠加顺序 | 服务与边界 |
| --- | --- | --- |
| 生产Supabase | pskit-agent-supabase；infra/supabase/docker-compose.yml → compose.cloud.yaml | PG17/Auth/Storage/Rest/Studio/Envoy/Realtime/Pooler/templates；127.0.0.1:18130 |
| 生产LiteLLM | pskit-agent-litellm；infra/litellm/compose.shared-postgres.yaml | 固定1.100.3；共享Supabase PG独立逻辑库；私网4000 |
| 生产API | pskit-agent-cloud；deploy/agent/compose.yaml → compose.cloud.yaml → compose.postgres.yaml | backend127.0.0.1:18088；AF3 callback127.0.0.1:18185；web是legacy-web profile |
| 生产OpenSandbox | 生产API组合 → compose.opensandbox.yaml → compose.opensandbox.production.yaml | 同API项目；Server高权限Docker控制面；独立internal runtime网络；保留rollout bind |
| 计算入口 | pskit-agent-compute-ingress；compose.compute-ingress.yaml | Caddy10.9.8.1:18186；已有cloud_app网络；AF3兼容Nginx18184 |
| A6000接收器 | pskit-agent-a6000-mcp；compose.a6000-mcp-receiver.yaml | 统一MCP/AF3；host network；journal/spool持久；受保护AF3 MCP18187 |
| StagingSupabase | pskit-agent-supabase-staging；supabase base → cloud → staging | 独立PG/卷/网络/账号，127.0.0.1:18131 |
| StagingLiteLLM | pskit-agent-litellm-staging；shared-postgres → staging | 独立gateway+model-stub，私网4002 |
| StagingAPI | pskit-agent-staging；agent base → cloud → postgres → staging [+opensandbox → opensandbox.staging] | 127.0.0.1:18090；合成MCP、mock AF3；独立workspace |
| 宿主Web | 非Compose；Nginx | 生产/var/www/agent.bioailab.net；Staging/var/www/agent-staging，私网18132 |
| 本地Docker | base → local | 旧联调入口；live已要求PG，模板仍有SQLite历史，需补synthetic render后验收 |

权威来源：stack.sh:52–71、staging_preflight.py:328–345、DEPLOYMENT.md:190–216及最近发布记录。日常release只能操作选定应用；stack up中的roles/schema初始化属于bootstrap/migrate。

## deploy/agent YAML 分类（源码引用）

| 文件 | 分类 | 说明 |
| --- | --- | --- |
| compose.yaml | active-prod / active-staging / active-dev | API基础；web为profile |
| compose.cloud.yaml | active-prod / active-staging | cloud参数 |
| compose.postgres.yaml | active-prod / active-staging | 共享PG接入 |
| compose.staging.yaml | active-staging | 隔离后端/替身 |
| compose.local.yaml | active-dev | 需校验当前PG前置 |
| compose.opensandbox.yaml | active-prod / active-staging | 控制面组件 |
| compose.opensandbox.production.yaml | active-prod | rollout/命名空间 |
| compose.opensandbox.staging.yaml | active-staging | 隔离rollout |
| compose.compute-ingress.yaml | active-prod | 独立计算回调入口 |
| compose.a6000-mcp-receiver.yaml | active-prod | A6000唯一统一接收器 |
| compose.a6000-receiver.yaml | rollback-only | AF3-only历史恢复；须核对journal |
| compose.release-registry.yaml | active-dev（可选发布基础设施） | 制品分发，不能因非常驻就删 |
| compose.a6000.yaml | migration-only | 旧A6000 API拓扑 |
| compose.frontend-cloud.yaml | migration-only | 旧云端前端容器 |
| compose.cloud-return.yaml | migration-only | 阿里云回迁叠加 |
| compose.supabase-relay.yaml | obsolete-candidate | 旧relay，不能据源码证明实例已删 |
| compose.sandbox.yaml | rollback-only / obsolete-candidate | 退役manager历史 |

infra/supabase的base/cloud/staging与infra/litellm的shared-postgres/staging为现行引用；infra/litellm/compose.yaml独立db为obsolete-candidate。deploy/agent/tests中的compose.*为active-dev测试fixture。

## Shell/安装脚本分类

| 文件（deploy/agent下） | 分类 | 使用限制 |
| --- | --- | --- |
| stack.sh | active-prod（bootstrap/status/render） | down/up涉及三个项目，不能普通release |
| staging.sh | active-staging | 独立测试项目和卷 |
| scripts/install_host_nginx_agent_cloud.sh | active-prod | 云端API宿主入口，需要授权root |
| scripts/install_host_nginx_staging.sh | active-staging | 私网Web安装 |
| scripts/install_private_admin_ingress.sh | active-prod | 管理入口私网边界 |
| scripts/enable_private_af3_ingress.sh | active-prod | A6000兼容回调 |
| scripts/disable_old_af3_ingress.sh | migration-only | 旧回调退役 |
| scripts/install_host_nginx_agent.sh | migration-only | 旧A6000 API入口 |
| scripts/check_gvisor_runtime.sh | active-prod / active-staging | 隔离能力验证需固定镜像 |
| scripts/install_gvisor_runtime.sh | active-prod / active-staging（bootstrap） | 主机runtime注册，非release |
| scripts/install_quota_volume_driver.sh | active-prod / active-staging（bootstrap） | 持久卷插件，非release |
| scripts/activate_opensandbox_host.sh | active-prod / active-staging（bootstrap） | 主机前置条件 |
| scripts/deploy_opensandbox_release.sh | active-prod / active-staging（executor发布） | active/unknown/namespace门禁 |
| scripts/rollback_opensandbox_release.sh | rollback-only | 保留state与恢复边界 |
| sandbox/entrypoint.sh | rollback-only / obsolete-candidate | 退役manager，不作为新默认 |
| 根deploy_to_cloud.sh | obsolete-candidate（不安全入口） | Phase1将无副作用拒绝执行 |

## 纯路径迁移前置

必须单独处理Docker COPY/context、Compose相对路径/source binds、OpenAPI导出根定位、TS生成输入、Pi extension路径、部署/测试fixture、CI workdir/cache、旧栈smoke与所有现行回滚入口。旧backend/frontend/根Dockerfile仍被旧CI/Compose/scripts引用；未完成这些前置不迁移、不删除。
