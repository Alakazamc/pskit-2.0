# Supabase 与开源替代方案核查（2026-10-01）

## 结论

Supabase 可以接管 PSKit 的**用户登录、会话和基于数据行的访问控制**，Postgres + pgvector 也能承载项目的小规模向量检索；但它不是现成的科研 Agent 运行时，也没有查到可直接为 **PSKit 的终端用户**按模型 token、API 请求或科研计算任务收费的原生产品。Supabase 的平台用量计费是 **Supabase 向项目所有者收费**，与 PSKit 向用户计费是两件事。[Auth](https://supabase.com/docs/guides/auth)、[AI & Vectors](https://supabase.com/docs/guides/ai)、[Supabase 平台计费](https://supabase.com/docs/guides/platform/billing-on-supabase)

| PSKit 所需能力 | Supabase 提供什么 | 边界 |
| --- | --- | --- |
| 登录、会话 | Auth 的用户、JWT、密码/OTP/OAuth 等 | PSKit 的任务归属、审批权限等业务规则仍须定义；直接访问数据库时可用 RLS。[Auth](https://supabase.com/docs/guides/auth) |
| 向量检索 | Postgres `pgvector`，支持语义、关键词与混合检索方案 | 迁移 Qdrant 的索引、过滤和检索质量需要单独验证。[AI & Vectors](https://supabase.com/docs/guides/ai)、[Vector columns](https://supabase.com/docs/guides/ai/vector-columns) |
| Agent | Edge Functions 可调用 LLM 或内置 AI 推理 API，亦可部署自定义 MCP server | 这是运行代码/对外暴露工具的设施，不是自动替代 Pi/Codex 等 Agent 会话与工具循环；Supabase 的官方 MCP/Skills 主要面向**开发者的编程 Agent**，官方明确说不要将开发者权限的 Supabase MCP server 交给产品终端用户。[Edge Functions](https://supabase.com/docs/guides/functions)、[AI models](https://supabase.com/docs/guides/functions/ai-models)、[AI Tools](https://supabase.com/docs/guides/ai-tools)、[官方 MCP 安全说明](https://supabase.com/docs/guides/ai-tools/mcp)、[自建用户 MCP](https://supabase.com/docs/guides/ai-tools/byo-mcp) |
| 科研长任务 | Edge Functions 能启动短后台任务 | 托管 Edge Functions 有时长、CPU、内存上限，官方建议重型长任务使用后台 Worker；AF3 等仍应留在独立 Worker/计算节点。[Functions](https://supabase.com/docs/guides/functions)、[Limits](https://supabase.com/docs/guides/functions/limits) |
| 终端用户 API 计量与收费 | 平台会计量**项目方**资源用量，可用 Edge Functions 接 Stripe webhook | 没有查到 Supabase 原生的“每个 PSKit 客户的用量账本、套餐、超额、发票”闭环。需自建或组合计量/支付系统；Stripe Wrapper 是访问 Stripe 数据的集成，不代表 Supabase 原生账单引擎。[平台计费](https://supabase.com/docs/guides/platform/billing-on-supabase)、[Stripe Wrapper](https://supabase.com/docs/guides/database/extensions/wrappers/stripe)、[Edge Functions](https://supabase.com/docs/guides/functions) |

## 自托管开源方案

| 方案 | 原生覆盖 | 关键边界与适用性 |
| --- | --- | --- |
| **Supabase self-hosted** | Auth、Postgres、Storage、Realtime、Functions；`pgvector` 可装在 Postgres | 单项目；云端的分支、托管备份/PITR、高级指标、Vector Buckets、平台管理 API 等不随自托管版提供。自行负责安全、数据库维护和备份。适合保留 SQL/RLS 生态，迁移成本仍不小。[自托管差异](https://supabase.com/docs/guides/self-hosting)、[Docker 要求](https://supabase.com/docs/guides/self-hosting/docker) |
| **Appwrite 2.0** | Auth、数据库、Storage、Functions、Realtime；官方现列出 VectorsDB/HNSW 检索，并提供自托管 | BSD-3-Clause。Appwrite 的应用数据库抽象与现有 SQLAlchemy 模型不同，不能假定无缝替换；其产品文档列出云端托管 PostgreSQL/pgvector 和 VectorsDB，自托管具体功能/版本要在 PoC 中确认。没有查到原生终端用户 API 计费闭环。[产品文档](https://appwrite.io/docs)、[VectorsDB](https://appwrite.io/docs/products/databases/vectorsdb)、[自托管](https://appwrite.io/docs/advanced/self-hosting)、[许可证](https://github.com/appwrite/appwrite/blob/main/LICENSE) |
| **Nhost** | Postgres、Hasura GraphQL、Auth、Storage、Node Functions | 主仓库 MIT，提供 Docker Compose 自托管示例；GraphQL/Hasura 是新引入的接口层，对当前 FastAPI/SQLAlchemy 项目迁移跨度大。示例 Compose 用标准 `postgres:16`，不能据此认定 pgvector 已预装；终端用户计费也需另配。[仓库/自托管](https://github.com/nhost/nhost)、[产品概览](https://docs.nhost.io/getting-started)、[Compose](https://github.com/nhost/nhost/blob/main/examples/docker-compose/docker-compose.yaml) |

这些是 **BaaS** 候选，不是 Pi/Codex Agent Server、科研 Worker 和用户计费的完整替代品。不存在已核实可“一键替换 PSKit 所有后端”的单一开源项目。

## 计费组合件

- **LiteLLM Proxy** 适合统一模型入口、虚拟密钥、模型费用追踪和预算限制。官方文档指出预算依赖数据库；无数据库部署时预算可能不生效。它计量的是模型请求，不会自动知道 AF3 等科研任务的真实成本，也不等于给客户开发票。部分代理功能标有企业许可，具体能力须逐项核对。[预算文档](https://github.com/BerriAI/litellm-docs/blob/main/docs/proxy/users.md)、[Docker 说明](https://github.com/BerriAI/litellm-docs/blob/main/docs/proxy/docker_quick_start.md)
- **OpenMeter** 是 Apache-2.0 开源的用量计量/权益/账单系统，官方方案可与 Stripe 联动收款。适合在需要套餐、额度、超额计费时追加；其自托管运行时还需要 Kafka、ClickHouse、Postgres，运维量明显高于把少量用量记录写入现有数据库。[仓库与许可证](https://github.com/openmeterio/openmeter)、[计量与权益](https://openmeter.io/docs/billing/entitlements/quickstart)、[Stripe 账单](https://openmeter.io/docs/billing/quickstart)

## 对 PSKit 的取舍

现有项目没有终端用户付费系统，因此“API 计费”应先明确计费对象：主模型 token、科研 GPU 作业、工具调用次数，还是组合套餐。若只是内部或少数用户，优先考虑现有 FastAPI + Postgres 中记录计量事件；待产生真实收费需求，再评估 OpenMeter/Stripe。若要减少自建登录、会话与数据权限代码，Supabase 是比整体搬到 Appwrite/Nhost 更贴近当前 SQL 结构的候选，但需要逐表设计 RLS、梳理服务端用户身份与后台任务授权。以上是基于文档和现有架构的推断，未经迁移 PoC 验证。
