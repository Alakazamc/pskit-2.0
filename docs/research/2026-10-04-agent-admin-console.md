# PSKit 管理台与模型开放策略调研

调研日期：2026-10-04。范围：读取现有源码、核对官方资料、提出接口对接方案。本文件中的管理台、角色模型、接口扩展和配置发布机制均为建议，尚未实施；没有修改服务配置、部署或数据库。

## 1. 建议结论

管理台继续采用 **React + Vite → Python 管理 API**。管理员使用现有 Supabase 身份登录，由 Python 校验管理员权限。管理页统一展示 LLM、科研模型服务、用户额度、沙箱和作业，但把真正的配置职责分清：

| 配置对象 | 配置事实来源 | PSKit 管理台承担什么 |
| --- | --- | --- |
| LLM 提供商、API key、具体部署、路由 | LiteLLM | 同步公开 alias，配置哪些用户/组可用、默认模型、图像/推理展示和下线策略 |
| 科研模型服务、MCP 工具、执行与计量契约 | PSKit 服务目录 | 注册、验证、审核、发布服务版本；授权 capability；展示作业和计量 |
| Skill | PSKit Skill 目录 | 配置指导文本、引用 capability 版本、用户/项目可用范围 |
| Token/GPU/CPU 配额和使用账本 | PSKit Python 业务层 | 调整限制、看预留与结算、解释异常、执行带理由的对账 |
| 身份、数据库运维 | Supabase / PostgreSQL | 关联用户身份，展示必要运营信息；深度数据库维护跳转 Studio |
| 沙箱和计算接收器 | PSKit 生命周期与任务服务 | 展示归属、状态、资源、活动会话、租约、待确认记录与排空操作 |

建议首版直接复用现有 React/Radix/TanStack Query 组件构建 `/admin` 模块。若后台 CRUD 页面快速增多，再引入 **Refine Core**。React-admin 是成熟备选，但不必为了几个后台页面同时加入第二套布局与表单体系。SQLAdmin 适合开发者查看数据，不能默认把业务账本开放成通用可编辑表。

上述选型是根据现有 SPA、复杂业务动作和权限需求作出的工程判断，不是官方文档声称某框架优于另一框架。

## 2. 现有实现：可以直接接的部分和缺口

以下结论来自本地源码快照；没有连接云端或读取生产密钥。

| 现状 | 事实依据 | 管理台对接含义 |
| --- | --- | --- |
| 已有管理 API，但认证是固定 `X-Admin-Key` | [admin.py](/home/jhli/pskit-2.0/new_backend/app/api/admin.py:40) | 可保留运维调用入口，浏览器管理台需另接管理员身份权限 |
| 已有依赖状态报告，刻意省略服务 URL、凭据和上游错误正文 | [dependency_probe.py](/home/jhli/pskit-2.0/new_backend/app/services/dependency_probe.py:71) | 可以复用状态数据；不能把它当作模型实际推理健康证明 |
| 已有每用户月 Token、每日 GPU 限制写入接口 | [admin.py](/home/jhli/pskit-2.0/new_backend/app/api/admin.py:119) | 后台表单可以对接；尚需管理员身份审计、更新版本和一个原子业务动作 |
| 当前限制修改分别调用 Token/GPU 存储事务，没有通用配置变更审计 | [usage.py](/home/jhli/pskit-2.0/new_backend/app/domain/persistent_conversation/usage.py:134)、[admin.py](/home/jhli/pskit-2.0/new_backend/app/api/admin.py:130) | 建议原子更新整组配额，并在同一事务记操作者、旧值、新值、原因 |
| 已有 AF3 GPU 对账和对账历史；活跃作业不能对账 | [admin.py](/home/jhli/pskit-2.0/new_backend/app/api/admin.py:58)、[af3.py](/home/jhli/pskit-2.0/new_backend/app/domain/persistent_conversation/af3.py:794) | 可作为“计量异常”页面基础，之后推广至通用科研作业 |
| 已有不可变 Skill 版本注册与每用户 Skill grants | [admin.py](/home/jhli/pskit-2.0/new_backend/app/api/admin.py:92)、[catalog.py](/home/jhli/pskit-2.0/new_backend/app/domain/catalog.py:240) | 不必重造 Skill 存储；需补草稿、审核和显式发布版本语义 |
| Skill grants 未设置时，当前表示不限制；工具再叠加游客策略 | [catalog.py](/home/jhli/pskit-2.0/new_backend/app/domain/catalog.py:305) | 对新科研服务建议明确“未授权默认不可用”，不要沿用隐式全开放 |
| `/models` 要求登录，但没有使用用户身份进行过滤 | [models.py](/home/jhli/pskit-2.0/new_backend/app/api/models.py:11) | 模型选择器目前没有逐用户/组模型目录 |
| `ModelCatalog` 是应用单例，使用一个配置的网关 key，读取 `/v1/models` 和 `/model/info`，缓存 60 秒 | [main.py](/home/jhli/pskit-2.0/new_backend/app/main.py:334)、[model_catalog.py](/home/jhli/pskit-2.0/new_backend/app/services/model_catalog.py:40) | 网关 key 可见集合不是 PSKit 用户授权集合；需增加 PSKit 发布策略 |
| 发送时校验 alias、推理等级和图片能力，但 alias 校验仍是全局集合 | [workspace.py](/home/jhli/pskit-2.0/new_backend/app/api/workspace.py:285) | UI 隐藏模型不足以限制使用，发送 API 必须再次校验用户模型授权 |
| 内部模型代理限制为当前 Run 的模型，并在调用前预留额度、把真实 Run owner 传给 LiteLLM | [internal.py](/home/jhli/pskit-2.0/new_backend/app/api/internal.py:143) | 必须保留这条受控代理链，管理页和工具页也不能绕过配额预留 |
| Supabase 身份由 Python 调 `/auth/v1/user` 校验；返回的 `UserIdentity` 没有管理员角色 | [auth.py](/home/jhli/pskit-2.0/new_backend/app/api/auth.py:51)、[supabase_auth.py](/home/jhli/pskit-2.0/new_backend/app/adapters/live/supabase_auth.py:293)、[models.py](/home/jhli/pskit-2.0/new_backend/app/contracts/models.py:7) | 登录已可复用，但管理员 RBAC 尚需增加 |
| MCP 服务目前来自环境 JSON，支持每服务工具白名单和密钥环境变量引用，启动时建 adapter | [config.py](/home/jhli/pskit-2.0/new_backend/app/config.py:315)、[main.py](/home/jhli/pskit-2.0/new_backend/app/main.py:285) | 还不是后台可动态保存、审核、切换版本的服务目录 |
| 多 MCP 服务已有 `server_id__tool_name` 命名与路由 | [multi_remote_mcp.py](/home/jhli/pskit-2.0/new_backend/app/adapters/live/multi_remote_mcp.py:26) | 可保留命名空间，别为同门同名 `predict` 工具制造冲突 |
| 当前计算资源契约仍为 AF3，包含 GPU 数量/显存而没有通用 CPU 计量字段 | [capabilities.py](/home/jhli/pskit-2.0/new_backend/app/contracts/capabilities.py:70) | “所有模型服务接入”需要通用能力/计量契约，不能仅扩几张表单 |
| 沙箱管理器源码是一用户一容器，固定 1 GiB/1 CPU/256 PIDs，30 分钟闲置停止 | [sandbox_manager.py](/home/jhli/pskit-2.0/new_backend/app/sandbox_manager.py:1)、[sandbox_manager.py](/home/jhli/pskit-2.0/new_backend/app/sandbox_manager.py:194) | 管理台配置租户资源档位是后续能力；不能把存在源码等同于生产已经启用 |
| 现有 React 应用未提供单独 admin 路由/权限体系 | [App.tsx](/home/jhli/pskit-2.0/new_frontend/src/app/App.tsx:75) | 当前工具/Skill 页面是用户工作区页面，不是运营控制台 |

现有后端使用 psycopg 连接池和私有 PostgreSQL schema，并非直接使用 SQLAlchemy ORM。因此 SQLAdmin 的标准 `ModelView` 接入还会增加一套 ORM 映射，而不是零成本挂载。[postgres.py](/home/jhli/pskit-2.0/new_backend/app/db/postgres.py:15)

## 3. 现成管理界面比较

| 方案 | 官方能力 | 对本项目的判断 |
| --- | --- | --- |
| React-admin | `dataProvider` 对接 API，`authProvider` 对接身份和权限；内建资源列表、表单和常用 CRUD。[数据提供者](https://marmelab.com/react-admin/DataProviders.html)、[授权](https://marmelab.com/react-admin/Permissions.html) | 表格表单很多时适合；数据 provider 必须对接 Python，禁止把运营表直接映射成浏览器写库。细粒度 RBAC 组件的一部分属于 Enterprise，不能假定全部免费 |
| Refine Core | 通过 data/auth/access-control provider 接外部系统，可结合自己的 UI 和路由。[Core](https://refine.dev/core/docs/core/refine-component/)、[数据提供者](https://refine.dev/core/docs/data/data-provider/)、[授权](https://refine.dev/core/docs/guides-concepts/authorization/) | 与现有 Vite/Radix 视觉体系较易协调；仍要写后台 UI 与 API adapter。已有 TanStack Query 时要规划 QueryClient/provider 边界，避免状态重复 |
| SQLAdmin | FastAPI/Starlette + SQLAlchemy/SQLModel 管理界面；支持自定义认证，默认不强制认证。[项目与说明](https://github.com/smithyhq/sqladmin)、[认证](https://github.com/smithyhq/sqladmin/blob/main/docs/authentication.md) | 快速内部只读数据查看适合；标准 CRUD 会通过 ORM 更新/提交记录，PSKit 的预留、结算、任务取消、对账不能交给任意表编辑。可以自定义业务动作，但做完这些就不再是开箱即用。依据其 [写入路径源码](https://github.com/aminalaee/sqladmin/blob/main/sqladmin/_queries.py) 作此推断 |
| Supabase Studio | 管理自托管 Supabase 项目的 dashboard，含数据库等项目运维入口。[官方自托管说明](https://supabase.com/docs/guides/self-hosting/docker)、[Studio 源码说明](https://github.com/supabase/supabase/blob/master/apps/studio/README.md) | 保留给平台运维；不承担 PSKit 科研作业、GPU 账本、模型开放策略、沙箱排空。不要让同门拿 Studio 权限完成日常模型注册 |
| LiteLLM Admin UI / MCP Gateway | 配置 Models + Endpoints、提供商 key、Test Connect，以及 key/team 模型限制与预算；MCP Gateway 已支持统一入口、发现/调用和工具访问限制。[Quickstart](https://docs.litellm.ai/docs/proxy/docker_quick_start)、[模型访问](https://docs.litellm.ai/docs/proxy/model_access)、[MCP Overview](https://docs.litellm.ai/docs/mcp/)、[预算](https://docs.litellm.ai/docs/proxy/users) | 保留已有页面；PSKit 首版读取目录、授权和发布 alias。MCP 目录和路由也可接现成网关，但 GPU/CPU 资源预留、服务执行证据、待回传对账和 Agent 唤醒仍需 PSKit 业务契约 |

前端框架的权限 provider 用于菜单、按钮和页面显示。最终授权必须由 Python API 执行，浏览器 `canAccess` 的结果不是可信授权依据。

LiteLLM 官方目前把某些高级能力标为 Enterprise，例如 key 的 per-model budget。本文仅建议使用公开文档可核对的基础模型 key/team 限制，不承诺已部署固定版本具备所有官网新功能；未来真正接入时须对照该固定镜像的 OpenAPI 和许可证核对。

### 3.1 LiteLLM 可以复用到什么程度

本次核对的最新官方资料已经包含 MCP Gateway：工具、Prompts、Resources、Streamable HTTP/SSE/stdio、按 key/team 等授予访问以及统一调用入口。因此“LiteLLM 只能管 LLM，不能管 MCP”是不准确的。它的配置参考还列出工具/描述/schema 固定能力，例如 `pinned_tools` 与服务 pin API，可以作为服务目录审核的候选基础。[MCP Overview](https://docs.litellm.ai/docs/mcp/)、[MCP Configuration Reference](https://docs.litellm.ai/docs/mcp_config_reference)

建议复用范围是：LLM 提供商部署和密钥、MCP 发现/路由、可用的 key/team 权限与调用日志。PSKit 仍拥有按真实用户进行的资源 admission、CPU/GPU reservation/settlement、远端执行 lease、接收器持久记录确认、Attempt 幂等及 Agent 自动唤醒。此次核对的上述网关文档没有给出能直接替代这些科研资源生命周期的完整契约；工具调用成本和网关请求时长不能自动视为 GPU 活跃时间或 CPU 进程时间。这是需求对接边界判断，不是声称 LiteLLM 不具备日志或 MCP 成本跟踪。

若后续采用网关 MCP，不要把同门科研服务同时注册在两处并分别维护开放策略。可以选 LiteLLM 为 transport registry，PSKit 存已审核 capability 的网关引用、schema digest、资源/计量和用户策略；也可以沿用现有 `MultiRemoteMcp` 路由。具体选择取决于已锁定版本的功能和授权，不能拿官网未来/当前文档直接当作生产镜像验收。[当前开源/Enterprise 对照](https://docs.litellm.ai/docs/enterprise)

LiteLLM JWT authentication 目前官方标为 Enterprise；该建议没有要求使用它。Python 保留 Supabase 用户验证，然后持服务端受限 virtual key 调网关，并传递服务器确认的用户信息。服务侧执行身份和用户资源权限仍由 Python 校验。[Client Setup 权限说明](https://docs.litellm.ai/docs/proxy/client_setup/overview)

## 4. 管理员身份与授权边界

### 4.1 不向浏览器发长期全局管理员密钥

建议沿用现有 Python 登录、刷新 Cookie 和短期 access token 流程。新增 `AdminPrincipal` 依赖：先通过 Supabase 验证身份，再根据 PSKit 私有表中当前有效的角色/范围授权。

建议角色是权限集合的名称，而不是散落在每个接口中的字符串比较：

| 角色 | 可执行操作 |
| --- | --- |
| `platform_admin` | 发布服务与策略、角色分配、全局运营动作 |
| `service_maintainer` | 只编辑自己负责的服务草稿、查看自己服务的诊断与作业；不能给自己增加公共授权 |
| `quota_operator` | 查看账本、调整额度、提交带原因的对账；不能读提供商 key |
| `auditor` | 只读配置版本与审计、脱敏使用信息 |

初始管理员由服务器受控引导命令绑定 Supabase user ID，不能让普通注册请求携带 `admin=true`。Supabase 支持通过服务端 Auth Hook 把角色放进 JWT 自定义 claims，但 **本项目首版建议以 Python 查到的当前角色为最终授权**，这样撤销管理员无需等旧 token 过期。claim 可以帮助 UI 初始显示，不能取自用户可编辑的 `user_metadata`。[Supabase RBAC](https://supabase.com/docs/guides/api/custom-claims-and-role-based-access-control-rbac)、[JWT 验证说明](https://supabase.com/docs/guides/auth/jwts)

保留现有 `X-Admin-Key` 时，只将它用于受控运维脚本，逐步收窄为独立服务账户权限；不要将它存入前端环境变量、localStorage 或管理页配置。Supabase service-role key、LiteLLM master key、MCP 上游凭据也只留在服务端。

### 4.2 用户模型访问的两个校验点

公开给用户的模型集合建议为：

```text
网关当前可用 alias
  ∩ PSKit 已发布且启用的 alias
  ∩ 当前用户/组获授权的 alias
  ∩ 当前用途允许的 alias（聊天/工具页 LLM 子任务）
```

1. `GET /models` 以当前 user ID 过滤并返回安全展示元数据。
2. `POST .../messages`、工具页创建 LLM Run 和内部模型代理执行时再次检查当前策略。

默认策略建议：新加入 LiteLLM 的 alias 先处于未发布状态；不因为管理员在 LiteLLM 新增了昂贵模型就自动向全部 PSKit 用户开放。用户 allowlist 的空集是无授权，`null` 是否继承组策略必须明确定义，避免空数组被误解释成任意模型。

LLM 工具页调用也应形成受用户身份约束的 Run/子 Run，通过相同内部代理预留、计量和结算。管理台按钮的“真实推理测试”要独立标明计费主体与额度，不能悄悄调用 master key 绕过账本。无计费的“连通检查”和可能花费的“推理检查”在 API 和 UI 上分开。

## 5. 统一服务目录：LLM、科研 capability、Skill 分开

建议抽象如下，均为待对接契约：

| 对象 | 最小字段 | 配置用途 |
| --- | --- | --- |
| `LlmAliasPolicy` | `alias`、`enabled`、`default_for`、`allowed_groups/users`、`reasoning_policy`、`revision` | 发布 LiteLLM alias；基础能力从网关导入，管理员只能收窄，不应随意把不支持图片的 alias 标为支持 |
| `ScientificService` | `id`、`owner`、`transport`、`endpoint_ref`、`credential_ref`、`network_zone`、`enabled`、`revision` | 绑定同门提供的 HTTP/MCP 服务和服务账户；不把原始凭据塞进工具参数 |
| `CapabilityRevision` | `id`、`service_id`、`version`、`input_schema`、`output_schema`、`schema_digest`、`execution_mode`、`resource_policy`、`metering_policy`、`allowed_groups/users` | 说明 Agent 能调用什么、怎样验证结果、怎样提交与计量 |
| `SkillVersion` | `id`、`version`、`instructions`、`capability_refs`、`visibility` | 指导 Agent 如何组合科研能力，不复制 GPU 排队/配额逻辑 |
| `ResourcePolicy` | `cpu_limit`、`memory_limit`、`gpu_count/profile`、`concurrency`、`max_runtime_seconds`、`max_reservation` | 声明服务执行资源、超时、共享方式和额度边界 |
| `MeteringPolicy` | `gpu_unit`、`cpu_unit`、`minimum_precision`、`source`、`charge_stages`、`rounding`、`revision` | 统一解释 CPU/GPU 耗时；不同模型不能各自随意定义“1 分钟” |

同门提交服务和 schema 草稿，管理者审核后发布。自动 `tools/list` 发现的 schema 只用于生成候选版本；服务更新 schema 不应该立即改变已授权能力。发布的版本固定 schema digest、执行和计量策略。作业保存具体版本和策略快照，便于追溯。

推荐目录详情能看到：服务责任人、alias/capability、输入输出 schema、资源声明、支持异步/取消情况、计量来源、最近连通检查和真实运行结果。注册界面只接受允许的网络域/区域，服务端负责地址策略与出站限制；不能通过任意 URL 配置让管理 API 变成无边界的内网 HTTP 转发器。

在此前提下，同门无需接触用户 JWT、平台管理员 key 或数据库。他们提交模型 API/MCP 入口，按受控 SDK 契约接受执行 token 和上报使用记录；包装器计时和资源限制由统一 SDK/执行层负责，管理台配置其策略。

## 6. 配额、作业和撤权行为必须可解释

### 6.1 管理操作保留业务账本

配额更改只更新额度，不修改已经使用的数字。新增 CPU 秒配额时建议内部以足够精度的整数微秒/毫秒保存、展示为秒；GPU 保留明确的 GPU 秒/分钟口径，展示与计费的舍入规则分开。

减少额度至低于 `已结算 + 已预留` 时：已有预留不删除，新提交停止；管理页显示超出部分。若要强制停止执行，使用独立作业取消命令，而不是把 reservation 行直接删掉。对账必须带 job/attempt、计量原始来源、操作者、理由、旧值和新值；重放不能重复扣费。

现在已有 AF3 的 `reserved/released/settled/pending_reconciliation/reconciled` 状态应被保留和通用化。界面不得将“没有 worker 心跳”自动显示成“GPU 已停止且额度已释放”。当前源码明确指出心跳缺失不是 GPU 进程结束证明。[计量状态契约](/home/jhli/pskit-2.0/new_backend/app/contracts/capabilities.py:82)、[计算租约规则](/home/jhli/pskit-2.0/new_backend/app/domain/persistent_conversation/af3.py:338)

### 6.2 停用模型、服务和用户的建议默认行为

| 动作 | 新提交 | 已排队 | 已运行 |
| --- | --- | --- | --- |
| 普通下线/撤权 | 拒绝 | 执行前重新校验；撤销并释放未执行的预留 | 默认允许当前已启动 attempt 结算；后续模型调用/新 attempt 拒绝 |
| 服务维护排空 | 拒绝或转移到已有兼容服务 | 保留为等待维护或明确取消 | 等活动作业结束后停接收器 |
| 紧急停止 | 拒绝 | 取消 | 发送停止命令并等待执行侧确认；无法确认时保留预留/待对账状态 |

以上是建议默认语义，应在确认接口时写入契约。HTTP 超时或断开请求无法证明远端模型进程已终止。停止 UI 应展示 `取消请求已发出/执行已停止/计量待确认` 的区别，而非一个乐观成功 toast。

沙箱普通重启/镜像更新也先检查活动 Pi Run 和写入，排空后执行。沙箱 volume 的删除与容器停止分成独立权限和操作；日常运营页面不要提供绕过 owner 校验的任意 Docker 命令输入框。容器共享边界最终取决于沙箱方案，管理台字段应使用 `tenant_id/isolation_mode/lease`，避免硬编码“目录即安全隔离”。

## 7. 管理页面建议导航

```text
管理台
├── 总览             依赖、容量、队列、异常结算
├── 语言模型         LiteLLM alias → 草稿/已发布/允许用户与组
├── 科研模型服务     服务 → capability 版本 → 资源/计量/开放策略
├── Skills           指导文本、能力引用、版本与授权
├── 用户与配额       Token/月、GPU/日、CPU/日、并发、上传额度
├── 沙箱             租户归属、隔离模式、活动会话、资源、排空状态
├── 计算作业         状态、attempt、worker、租约、日志、结果、取消确认
├── 计量与对账       使用账本、预留、待结算、待回传、人工修正
└── 配置与审计       草稿对比、发布记录、操作者、回滚与版本
```

语言模型页提供“打开 LiteLLM 运维台”的入口，但不 iframe 内嵌管理员凭据；Studio 也通过受保护的独立运维入口访问。普通用户工具页面只看获授权的科研模型，填写 schema 驱动参数，并通过 Python 建立作业/LLM 子 Run。

## 8. 最小管理 API 对接表

这是建议接口表，**不表示这些新增 API 已实现**。沿用 `/api/v1/admin` 和现有 Python 认证边界；分页返回 `items/next_cursor`，错误使用稳定 `code/request_id`，审计信息不带凭据。下列动作默认都校验 JWT + 当前 RBAC。

| 接口 | 状态 | 建议请求/响应要点 |
| --- | --- | --- |
| `GET /admin/me` | 新增 | 角色、scope、允许的管理操作、环境名；不返回管理密钥 |
| `GET /admin/dependencies` | 已有 key 入口 | 保留脱敏状态，增加 RBAC 入口 |
| `GET /admin/llm-aliases` | 新增 | 网关发现状态、发布版本、用户策略、能力元数据 |
| `PUT /admin/llm-aliases/{alias}/draft` | 新增 | `expected_revision`、启用、授权组/用户、默认用途；只保存草稿 |
| `POST /admin/services` | 新增 | 服务责任人、transport、endpoint/credential 引用、网络区域；产生草稿 |
| `GET /admin/services/{id}/discovery` | 新增 | 脱敏 `tools/list` 候选、schema digest、与当前已发布版本的差异 |
| `POST /admin/services/{id}/checks` | 新增 | 连通/schema 验证；真实计算检查另行指定 `test_subject` 与资源上限 |
| `PUT /admin/capabilities/{id}/draft` | 新增 | schema、版本、执行/资源/计量策略、可用用户/组 |
| `PUT /admin/skills/{id}/versions/{version}` | 已有 key 入口 | 保留不可变版本写入；增加 RBAC/审计，并补独立发布引用 |
| `GET /admin/users` | 新增 | 脱敏身份、组、状态、额度、用量汇总 |
| `PUT /admin/users/{id}/limits` | 已有 Token/GPU 入口 | 扩充 CPU、并发等字段时显式版本；原子更新、`reason/expected_revision`、审计 |
| `PUT /admin/users/{id}/grants` | 新增汇总 | LLM alias、capability、Skill/组；不等同于登录角色 |
| `GET /admin/sandboxes` | 新增 | 归属、隔离模式、活动 Run、idle、CPU/RAM、镜像版本；不泄露容器凭据 |
| `POST /admin/sandboxes/{id}/drain` | 新增 | 排空/停止意图与 request ID；不把 kill 执行当作立即完成 |
| `GET /admin/jobs` | 新增通用 | 服务、能力版本、owner、attempt、worker、租约、作业/计量状态 |
| `POST /admin/jobs/{id}/cancel` | 新增通用 | 理由、期望版本、取消意图；返回异步确认状态 |
| `GET /admin/usage/reconciliation` | 新增通用 | reservation、结算和接收器待回传/待对账项；游标分页 |
| `PUT /admin/af3/jobs/{id}/gpu-usage` | 已有 key 入口 | 保留终态检查与审计；以后使用通用 `/jobs/{id}/usage-reconciliation` |
| `POST /admin/config-releases` | 新增 | 固定草稿版本集合、验证结果、Staging 环境 ID、预计影响与 reason |
| `POST /admin/config-releases/{id}/publish` | 新增 | 幂等键、期望当前版本；保存发布动作及传播状态 |
| `GET /admin/audit-events` | 新增 | 操作者、动作、对象版本、旧新值摘要、结果、关联请求；不含 secret |

这里 `credential_ref` 是服务端秘密存储的引用，不是让同门把 key 写在 JSON 中。首版 LLM 凭据继续在 LiteLLM 配置；科研服务账户凭据可以由受控登记流程提供，只向维护者显示名称、范围、到期时间和脱敏指纹。

前后端若未来进入实现，可先固定 Pydantic/OpenAPI 和 mock 响应，再生成 TypeScript 类型；真实权限校验必须在 Python 中验证，界面 mock 只用于布局与交互对接。

## 9. 发布、版本和运行态配置

建议配置走 `draft → validated → approved → published → retired`。发布包是不可变的对象版本集合；管理员修改授权、资源上限或 schema 都生成新 revision。

1. 草稿保存时验证 schema、命名、工具引用、资源和计量字段。
2. Staging 校验服务发现和替身调用；若是同门真实 GPU 服务，必须明确使用测试主体及单独上限，不能混入生产用户账本。
3. 显示受影响用户、活跃/排队作业、策略差异及是否需要排空。
4. Python 事务记录新配置版本、操作者和发布意图；后台 adapter 按版本重建或刷新，公布传播状态。
5. 旧任务继续引用旧 capability/计量快照，实时撤权仍按第 6 节规则检查。
6. 回滚生成一次新的发布事件，不能删除已产生的使用或对账历史。

如果一个发布动作同时改 PSKit 策略和 LiteLLM，不能假设两个数据库原子提交。首版推荐把 LiteLLM 提供商部署留在原 UI，PSKit 仅发布已验证存在的 alias；后续若统一管理，需要幂等 outbox、每项同步状态和失败补偿，而不是先展示成功再后台尽力修改。

当前全局 `ModelCatalog` 存在发现失败时默认 alias 回退以及 60 秒缓存行为；引入授权后不能利用这个回退绕过“未发布/已撤销”的策略。可以缓存网关发现数据，但用户权限按策略版本求交集；未能确认新 alias 时保守不可用。[model_catalog.py](/home/jhli/pskit-2.0/new_backend/app/services/model_catalog.py:56)

## 10. 对接顺序与尚待确认的边界

建议先确认一份能力注册样例和计量单位，再确认管理 API。这样后台页面管理的是可信业务对象，而不是给 Docker、LiteLLM 和数据库各套一层按钮。

推荐顺序：管理员 JWT/RBAC → LLM alias 发布与用户过滤 → 科研服务/capability 注册 → 通用 CPU/GPU 账本 → 沙箱/作业只读运营视图 → 发布/排空/对账动作。现阶段只形成对接资料，不实施。

后续规格需要明确：

- 租户隔离单位是个人、实验组还是受信共享池；“一个容器多个用户”不能仅依赖不同工作目录隔离。
- 同门模型是否允许并发共享 GPU，计量是独占设备时间、分配的 GPU 秒还是采样活动时间。
- 配额周期的时区、舍入、失败/取消的收费阶段，以及服务侧无法证实停止时如何结算。
- 服务维护者能看哪些用户输入、日志和结果；全局管理权限不自动授予所有科研数据读权限。
- 管理台是否需公共 HTTPS，还是先限定 WireGuard；这是入口控制选择，不替代 API 身份和 RBAC。

## 官方来源索引

- React-admin：[DataProvider](https://marmelab.com/react-admin/DataProviders.html)、[Authorization](https://marmelab.com/react-admin/Permissions.html)。
- Refine：[Core](https://refine.dev/core/docs/core/refine-component/)、[Data Provider](https://refine.dev/core/docs/data/data-provider/)、[Authorization](https://refine.dev/core/docs/guides-concepts/authorization/)。
- SQLAdmin：[现项目官方仓库](https://github.com/smithyhq/sqladmin)、[Authentication](https://github.com/smithyhq/sqladmin/blob/main/docs/authentication.md)、[标准 CRUD 写入源码](https://github.com/aminalaee/sqladmin/blob/main/sqladmin/_queries.py)。原 `aminalaee/sqladmin` 主页已跳转到 `smithyhq/sqladmin`，未来锁版本时应核对包来源与对应标签。
- Supabase：[自托管 Docker/Studio](https://supabase.com/docs/guides/self-hosting/docker)、[JWT](https://supabase.com/docs/guides/auth/jwts)、[Custom Claims / RBAC](https://supabase.com/docs/guides/api/custom-claims-and-role-based-access-control-rbac)。
- LiteLLM：[Admin UI 模型配置 Quickstart](https://docs.litellm.ai/docs/proxy/docker_quick_start)、[Model Access](https://docs.litellm.ai/docs/proxy/model_access)、[Budget / Rate Limits](https://docs.litellm.ai/docs/proxy/users)、[RBAC](https://docs.litellm.ai/docs/proxy/access_control)、[MCP Overview](https://docs.litellm.ai/docs/mcp/)、[MCP Configuration Reference](https://docs.litellm.ai/docs/mcp_config_reference)、[OSS / Enterprise 对照](https://docs.litellm.ai/docs/enterprise)。
