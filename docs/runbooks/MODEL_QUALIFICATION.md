# Model Runtime Profile 与供应商资格

本说明对应 Phase 3 的本地实现。未连接真实供应商、未修改模型密钥、未部署
Staging 或生产。已有 PinCC 路线保留；普通 OpenAI-compatible 路线不应用其补丁。

## 现有路由与候选 Profile

`admin_models` 的 `draft_json`、`published_json`、revision 和审计继续作为权威，
不增加模型注册数据库。`ModelDraft.runtime_profile` 是可选字段。只对服务启动前
已经持久发布过的无 Profile 路由保留 grandfather 兼容，允许普通编辑及停用后重发；
新 alias 省略 Profile、或把未验收候选的 Profile 改为 None，不能发布或获得可见性。
旧客户端管理已存在路由仍可工作；这条兼容路径不代表新供应商已通过工具资格。

显式 Profile 定义协议、显示名称、供应商标签、tools/tool streaming/images、推理
等级、context/output 上限以及封闭的 wire policy。UI 的 `off` 在资格及 Pi 请求中
统一映射为 Chat Completions 的 `none`，证据里的 case 仍是 `reasoning:off`。
当前只实现 Chat Completions；
声明 Responses 不会使该协议可用。用户不能提交 endpoint、provider key、Profile
或验收证据来覆盖 Run 模型配置。

提交草稿时，服务端重置资格状态及证据，并分配候选 revision。Probe/Publish 的
审计 revision 可以继续递增，候选 revision 保持不变。编辑新草稿不会撤销仍然
发布的旧候选；新候选必须重新验收。用户/组/purpose 权限仍由每次模型调用重新核对。
一旦成功发布显式 qualified Profile，该 alias 退出 grandfather 集合；同一进程或
重启后均不能清空 Profile 来降级绕过资格。仅编辑失败候选不会撤销仍发布的旧路由。

图片能力按所有已知部署的最低能力计算。任何已知 false 或缺失部署元数据不能被
旧 operator 图片覆盖推广为整个混合路由支持图片。一个缺少元数据的旧 generic
alias 仍保留 operator fallback；显式人工 Profile 要获得真正工具资格才可发布。

## 管理接口

`POST /api/v1/admin/llm-aliases/{alias}/qualify` 要求 `models:publish` 权限，且只
接收现有 `RevisionRequest` 的 `expected_revision`、`reason`。包含 `/` 的普通及
URL-encoded alias 均可用于草稿、验收、发布和停用；OpenAPI 模板名称不变。

默认应用的 `app.state.model_qualifier` 为 None，返回 `MODEL_QUALIFIER_UNAVAILABLE`。
这是失败关闭的边界，不能把它解释为已经完成真实供应商资格。管理员提供的
`passed` 状态和结果不被接受；草稿里的资格字段也会被服务端清空。

## 受信任 Probe 的接入合同

服务器可配置 `ModelQualification`，注入受控 Probe：

```python
async def probe(case_id, body, *, actor_id, deployment_fingerprint):
    # 服务器解析配置好的目标；API 调用者不提供 URL、key 或任意 prompt。
    # 调用前核对当前授权，并预占本次测试的独立限额。
    # 返回真实协议结果；故障注入由隔离测试通道产生。
    ...
```

受控 `ModelQualificationDriver` 已接入应用工厂；实际启用仍是独立的受控 Staging 工作。本轮没有接入付费网关。
非 offline scope 必须提供服务器的 `budget_check(actor_id, alias, body)`，允许
同步或异步检查；每次 Probe 前重检。限额失败立即终止，无后续调用或虚构零用量。
调用总耗时最大 120 秒，默认 60 秒；单结果默认 256 KiB、最大 1 MiB。
超时及限额失败分别返回稳定脱敏错误，不保存不完整的成功证据。

固定测试涵盖文字、结束帧、工具 JSON、分片 id/name/arguments 聚合、关联 tool
result 续聊、usage 已知/未知、图片、每个声明的推理等级、401/402/403/429/5xx/
超时，以及所有部署路由。没有真实结构化工具调用时不会构造伪造工具结果续聊。
普通 `<write_file>` 文本不是工具调用。未知 usage 保持 unknown，不写成零。

## 证据与 Agent Ready

证据只有 alias/candidate revision、profile/deployment/provider-URL/request/response
指纹、Pi/gateway 版本、时间、case 状态及 scope；不存储实际请求、私密输出、key
或原始 provider 错误。配置的上游 model/api_base 及部署元数据参与指纹，provider
key 不参与。后续路由或能力变化使显式发布 Profile 失效，需刷新 discovery 再验收。
网关/Pi/兼容层升级仍需固定版本门禁和独立重验，不能仅沿用旧报告。

offline/gateway 资格不会通过 `workspace_artifact`，状态为 degraded；没有此项的
报告不授予 Agent Ready。只有 staging scope 的受信任真实 Probe 核实
`write_file → read_file → artifact.created`、用户所有权及只结算一次，且 tools、
streaming tools、图片/推理声明、错误/所有部署验证均通过，才可形成 passed 候选。
该 staging Probe 不能通过管理页面自报布尔值来代替真实 Run/下载/账本验证。

发布和每次授权均核对 candidate hash、alias/revision、证据 ID、所有必须 case、
当前 deployment fingerprint 和已知不兼容元数据。显式未验收 Profile 不进入
Agent Ready。启动前已持久发布的旧无 Profile 路由继续可用，作为现有功能的兼容边界。

## 待授权的真实验收与回滚

在隔离 Staging 中用固定 Pi/LiteLLM/compat 版本、测试账号、独立 key 和小预算，
为 alias 的每个可达部署运行真实工具/读回/产物/鉴权下载/usage 验证。记录版本与
指纹，并人工审核后发布。仅 HTTP 200、离线 mock 通过或文本声称写入均不算验收。
本轮该步骤为 **not tested**。

回滚采用单一职责代码提交 revert，并停用新候选/重发之前合格候选。没有新表、
schema 迁移或历史数据删除；不要通过普通代码回滚降级数据库或修改模型密钥。

## 已实现的服务器 Driver（默认关闭）

`ModelQualificationDriver` 复用当前 ModelCatalog、ModelPolicy、Run/Token guard、Pi
及 WorkspaceTransfer；不是第二个网关或独立执行器。调用者不能传任意probe、URL、
key、费用上限或成功布尔值。internal代理在原HMAC、owner、model及route override
检查之后委托已登记的资格Run；普通Run返回None继续原公开路径与权限检查。

仅服务器配置启用：

| 环境项 | 默认/启用约束 |
| --- | --- |
| `RESEARCH_AGENT_MODEL_QUALIFICATION_ENABLED` | false；不自动开启 |
| `RESEARCH_AGENT_MODEL_QUALIFICATION_ENVIRONMENT` | offline；显式启用必须staging |
| `RESEARCH_AGENT_MODEL_QUALIFICATION_USER_ID` | 空；单独已验证正式审计账号，不能是操作管理员 |
| `RESEARCH_AGENT_MODEL_QUALIFICATION_PI_VERSION` | unverified；核对固定Pi0.87.1实际制品 |
| `RESEARCH_AGENT_MODEL_QUALIFICATION_GATEWAY_VERSION` | unverified；核对固定LiteLLM1.100.3实际制品 |

应用工厂同时要求live、managed policy、Pi runtime与sandbox执行。Driver重新核对
实际SandboxPiRunner、已配置工作区provider/attempt/transfer，以及账户tier。
localhost/injected/mock transport永远为offline；不能仅填写staging字符串就签发
真实通过证据。没有安装宿主Pi来替代Staging沙箱路径。

每个当前deployment做协议矩阵，用真实 `x-litellm-model-id`、网关版本及api-base
响应头和新鲜discovery核实路由。默认官方路由若缺少可验证api_base/头会失败关闭，
管理员须先提供受控网关元数据/资格适配证据；不是通过猜测provider或body重写推广。
每次调用重检候选revision、指纹和管理员write/publish权限。证据不保存请求/输出文本。

每个实际上游POST有独立内部Run和模型call guard；费用走审计账号真实额度，成功
usage结算一次。缺失/截断/取消/未知错误保持保守unknown，绝不写成零。默认单次
验收最多48次调用、32768估算token、120秒、16KiB请求及256KiB响应；构造器可由
受信服务器在封闭上限内调整，管理请求不能覆盖。错误401/402/403/429/5xx/timeout
用本地归一化故障通道测试，避免真的对供应商发无效凭据/收费错误请求。

工作区验收要求真实Pi工具event、固定write/read参数、实际文件字节、持久化产物
metadata/hash、本账号下载、异账号拒绝和真实账本。仅工具名称、文本声称文件存在
或管理页面自报passed不算。离线实际安装Pi只为合成隔离回归，offline scope即使
工具成功也不能获得Agent Ready。

本轮未填写以上真实配置、没有Staging/生产验收或付费调用。审计会话留在专用账号
按既有retention处理；不删除已有审计历史。启用前还须人工确认固定版本、独立小额
provider key、审计账号额度、受控网关头与真实沙箱边界，然后运行管理验收、审核
证据、再发布候选。供应商/版本/路由变化后重新验收。

并发准入在预留事务前原子占用 attempt/Token 上限；取消不恢复已消耗的测试预算。退出验收工作区会撤销代理登记、取消并 drain 剩余请求；未知用量留待对账。内部 `purpose=model_qualification` 的崩溃 Run 直接失败，普通初次领取/续跑都排除该 purpose，避免超出验收预算自动重试。真实文件验收须核对已持久化的 `artifact.created` 事件，不仅检查 Artifact 数据行。
