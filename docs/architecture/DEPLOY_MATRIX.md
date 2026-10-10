# Phase 0 部署矩阵

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
