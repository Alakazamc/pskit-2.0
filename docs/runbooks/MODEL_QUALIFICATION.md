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

默认应用未配置 `app.state.model_qualifier`，返回 `MODEL_QUALIFIER_UNAVAILABLE`。
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

Probe 的实现和启用仍是独立的受控 Staging 工作。本轮没有自动接入付费网关。
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
