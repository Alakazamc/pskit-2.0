# CORAL MCP 自动接入研究

日期：2026-10-06。本文区分源码已实现、协议事实与建议设计；静态阅读不代表线上部署验证。

## 现有 PSKit 与协议

### 已有基础与具体缺口

PSKit 已具备 MCP schema 发现、版本化计算能力、受权限约束的人工发布和简易 schema 表单；但目前尚未形成“输入 URL → 自动建草稿 → 执行验收案例 → 人工审核 → 发布即运行”的完整链路。

| 用户目标 | 源码现状 | 仍需补足 |
| --- | --- | --- |
| 只填 MCP URL | 管理端存 `endpoint_ref`，后台映射预先批准的地址；MCP 发现还要求预置 `allowed_tools`。保存草稿必须已有至少一个 capability。 | 独立于服务草稿的 URL 探测入口；发现后选工具、自动生成草稿及允许列表；有鉴权时还需要凭据流程。 |
| 自动发现工具参数 | `initialize` 后执行一次 `tools/list`，读取 `inputSchema` 和可选 `outputSchema`。 | 处理分页；保存协议能力、工具描述和必要元数据；区分远端输出与业务结果 schema。 |
| 验收通过再审核发布 | 有 connectivity/schema 检查、版本校验、发布权限、审计、不可变发布版本。 | 持久化验收案例与运行结果；版本绑定的验收门槛；审核记录。现有 validated 仅意味着 schema 相等。 |
| schema 自动生成 UI | 通用 MCP 页面按顶层 properties 渲染简单输入框；CORAL 有专门页面。 | 默认值、枚举、范围、嵌套对象、数组、联合类型、文件与领域输入控件；通用计算任务页及结构化结果组件。 |
| 发布即可执行 | 发布注册 compute capability；独立 receiver 通过受信任 Python `module:object` 加载执行器。 | 将已发布服务版本与通用 MCP 执行器配置绑定，并验证 worker 就绪。 |

表中判断的源码依据分别见 [endpoint 解析及 MCP allowlist](../../new_backend/app/domain/admin/releases.py#L75)、[ServiceDraft 必填字段](../../new_backend/app/contracts/admin.py#L55)、[MCP 发现](../../new_backend/app/adapters/live/remote_mcp.py#L82)、[校验与发布](../../new_backend/app/domain/admin/releases.py#L240)、[通用表单](../../new_frontend/src/features/mono/GenericToolPage.tsx#L9)、[receiver 加载](../../new_backend/scripts/compute_receiver.py#L19)。

### 管理端：已有审核入口，但当前校验没有执行案例

API 已有 `PUT /services/{id}/draft`、`POST /services/{id}/discovery`、`POST /services/{id}/checks`、`POST /config-releases` 和 `POST /config-releases/{id}/publish`。读、写、发布分别检查 `services:read/write/publish`。这是可复用的权限和发布基础。[admin_services.py:22](../../new_backend/app/api/admin_services.py#L22)

地址来自 `RESEARCH_AGENT_ADMIN_SERVICE_ENDPOINTS_JSON`，管理草稿里的 `endpoint_ref` 必须命中已批准的服务端映射；MCP 模式还必须配置非空 `allowed_tools`。因此当前页面中的 endpoint 字段不是可直接粘贴任意 MCP URL 的入口。[config.py:177](../../new_backend/app/config.py#L177)、[releases.py:75](../../new_backend/app/domain/admin/releases.py#L75)、[releases.py:156](../../new_backend/app/domain/admin/releases.py#L156)

当前保存需要手工提供 `model_version` 与非空 capabilities 列表；管理前端是 JSON 文本域。发现结果仅在 `<pre>` 中展示，没有导入、勾选工具或合并草稿动作。只有 schema 检查成功才更新页面中的 validated 状态。[admin.py:55](../../new_backend/app/contracts/admin.py#L55)、[AdminServicesPage.tsx:26](../../new_frontend/src/features/admin/AdminServicesPage.tsx#L26)、[AdminServicesPage.tsx:42](../../new_frontend/src/features/admin/AdminServicesPage.tsx#L42)

`check()` 比较服务端发现的 input/output schema 与草稿字段是否逐项相等；discovery 本身不把草稿置为 validated。检查种类仅有 connectivity/schema，没有 test case、期望输出、验收运行、评审结论等字段。故当前按钮“检查通过”不能作为 CORAL 科学计算验收通过的证据。[releases.py:250](../../new_backend/app/domain/admin/releases.py#L250)、[releases.py:328](../../new_backend/app/domain/admin/releases.py#L328)、[admin.py:80](../../new_backend/app/contracts/admin.py#L80)

发布要求服务版本仍为同一 revision、状态 validated 且存在 schema_digest；同一事务注册 capability、更新 published_revision、写 outbox 与审计。源码中没有要求真实案例通过，也没有要求发布者与提交者为不同人员。现有基础可以承载人工发布，但“独立审核”“案例合格”需要另建明确的数据与状态门槛。[releases.py:349](../../new_backend/app/domain/admin/releases.py#L349)、[releases.py:384](../../new_backend/app/domain/admin/releases.py#L384)

### 计算契约：MCP 是传输方式，PSKit 另有任务与计量协议

`contracts/compute.schema.json` 是 PSKit 计算领域契约，包含版本、input/output schema、资源用量要求、预算、并发、超时、取消模式等；它不是根据任意 MCP URL 自动生成的服务专属完整配置。`CapabilityVersion` 采用禁止额外字段的契约，现有类型没有 acceptance cases、UI schema、远端工具绑定或 MCP Tasks 能力字段。[compute.schema.json:55](../../contracts/compute.schema.json#L55)、[compute.py:17](../../new_backend/app/contracts/compute.py#L17)、[compute.py:66](../../new_backend/app/contracts/compute.py#L66)

SDK `ComputeService.compute_tool()` 已能根据 Python 函数参数类型注解生成 input schema，再导出 `ComputeServiceManifest`；output schema 默认只是 `{type: object}`，需要维护者显式补充。这降低了新 Python 服务的接入成本，但不能据此推断任意既有 MCP 具备 PSKit 的任务或计量字段。[service.py:38](../../new_backend/pskit_compute/service.py#L38)、[service.py:60](../../new_backend/pskit_compute/service.py#L60)

PSKit 执行返回为 `Completed(result, usage, artifacts)`、`Pending(job_id)` 或 `Failed(error, usage, artifacts)`；终态必须有 UsageReport，已声明的资源指标必须存在且来源必须符合策略。GPU 能力必须要求 gpu_device_ms。普通 MCP 文本/JSON 结果不会自动满足这一计算契约。[compute.py:21](../../new_backend/app/contracts/compute.py#L21)、[compute.py:36](../../new_backend/app/contracts/compute.py#L36)、[compute.py:85](../../new_backend/app/contracts/compute.py#L85)、[metering.py:10](../../new_backend/app/domain/compute/metering.py#L10)

`McpAdapter` 要求配置 submit/status/cancel 工具名，从 structuredContent 或单个 JSON 文本块中解析 PSKit ExecutionReport；提交仅传 `grant.job.arguments`，轮询与取消传 `{job_id}`。它没有自动根据工具描述推断工作流，也没有 Tasks 协议实现。[mcp_adapter.py:1](../../new_backend/pskit_compute/mcp_adapter.py#L1)、[mcp_adapter.py:19](../../new_backend/pskit_compute/mcp_adapter.py#L19)、[mcp_adapter.py:41](../../new_backend/pskit_compute/mcp_adapter.py#L41)

发布只注册 ComputeServiceManifest，其中没有 endpoint 或 executor 字段。现有 receiver 则从命令行 `--executor module:object` 加载 Python 对象；本轮对 `admin_config_outbox` 的全仓搜索只找到定义、迁移和发布写入，未找到消费端。因此不能把“服务已发布”表述为“系统已经动态创建并启动可执行的 MCP worker”。[releases.py:407](../../new_backend/app/domain/admin/releases.py#L407)、[compute.py:100](../../new_backend/app/contracts/compute.py#L100)、[compute_receiver.py:19](../../new_backend/scripts/compute_receiver.py#L19)

### 必须先澄清的 output schema 层级

MCP `outputSchema` 约束工具响应的 `structuredContent`。PSKit `CapabilityVersion.output_schema` 则在 `validate_report()` 中约束 `Completed.result`。管理 discovery 目前将前者原样映射成 output_schema，并要求它与后者完全相等，造成语义层级不一致。[MCP Tools 官方规范](https://modelcontextprotocol.io/specification/2025-11-25/server/tools#output-schema)、[remote_mcp.py:96](../../new_backend/app/adapters/live/remote_mcp.py#L96)、[releases.py:264](../../new_backend/app/domain/admin/releases.py#L264)、[metering.py:21](../../new_backend/app/domain/compute/metering.py#L21)

具体推论：如果 CORAL 的 structuredContent 是完整的 `{status, result, usage}`，它的 MCP outputSchema 就应描述该完整对象，而 PSKit 的业务 output_schema 应描述内部 result；复制完整 schema 会使终态业务校验错位。如果远端返回裸科学结果，现有 McpAdapter 又不能将它直接解析成 ExecutionReport。建议在绑定中明确保存 `remote_output_schema`、`result_schema` 和有限的结果提取/封装规则；或者让服务额外提供 PSKit manifest，明确这两层的含义。此段为根据源码与规范得出的设计建议，尚未实施。

另一个互操作边界：MCP outputSchema 是可选项；现有 capability 的 output_schema 默认非空对象 schema，而管理 schema 检查将未提供的远端输出 schema 视为 `None` 并比较相等，因此缺失 outputSchema 的 MCP 工具不能按现有默认流程校验通过。接入界面应将“没有远端输出契约”作为需补充或受限接入的明确状态。[remote_mcp.py:97](../../new_backend/app/adapters/live/remote_mcp.py#L97)、[compute.py:70](../../new_backend/app/contracts/compute.py#L70)、[releases.py:267](../../new_backend/app/domain/admin/releases.py#L267)

### schema 生成 UI：基础可复用，科学工作台尚需声明式配置

通用 `GenericToolPage` 按 input_schema.properties 渲染 boolean 下拉框、数字或文本输入框，并处理顶层 required；array/object 通过用户输入 JSON 字符串解析。代码没有处理 enum、default、minimum/maximum、pattern、递归对象、`$ref`、联合类型或文件控件；结果按 JSON 展示。它属于简易通用表单，不是完整 JSON Schema 渲染器。[GenericToolPage.tsx:9](../../new_frontend/src/features/mono/GenericToolPage.tsx#L9)、[GenericToolPage.tsx:71](../../new_frontend/src/features/mono/GenericToolPage.tsx#L71)

CORAL 当前走专用 `CoralWorkspace` 与 compute job API，固定能力名 `coral.generate_rna`，固定 pdb_id、chain、num_samples、length 字段；只从能力 schema 读取数量和长度的 minimum/maximum。目录也显式判断 CORAL 并渲染专用组件。现有 CORAL 界面不能证明新科学模型仅靠 input/output schema 就可获得同样的工作台。[coralResult.ts:3](../../new_frontend/src/features/coral/coralResult.ts#L3)、[CoralWorkspace.tsx:16](../../new_frontend/src/features/coral/CoralWorkspace.tsx#L16)、[CoralWorkspace.tsx:55](../../new_frontend/src/features/coral/CoralWorkspace.tsx#L55)、[ToolPages.tsx:47](../../new_frontend/src/features/mono/ToolPages.tsx#L47)

建议把产品承诺分成两层：标准 schema 覆盖的服务可零手写页面获得表单、运行状态、通用结果和历史；科学工具再通过版本化 UI 配置选择 PDB/序列/文件输入、表格/序列/结构结果组件及下载行为。若协议缺少作业、计量、文件或领域含义，仍需一次适配；不能让 AI 凭字段名猜测这些语义。此为建议，不是现有能力。

### MCP 官方协议可依赖的边界

- 自动发现入口是完成初始化后的 `tools/list`，它支持分页；inputSchema 描述调用参数，outputSchema 可选，约束 structuredContent。PSKit 当前只调用一次 list_tools 且没有跟随 nextCursor，也未将输出 schema 纳入面向通用 UI 的 McpTool DTO。[MCP Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)、[remote_mcp.py:88](../../new_backend/app/adapters/live/remote_mcp.py#L88)、[remote_mcp.py:108](../../new_backend/app/adapters/live/remote_mcp.py#L108)
- MCP 不规定具体 UI 形式；基于 schema 生成基础表单是 PSKit 的实现选择。仅凭标准工具 schema 不能证明某字段代表 PDB 文件或 GPU 毫秒，也不能证明科学案例正确。[MCP Tools 用户交互说明](https://modelcontextprotocol.io/specification/2025-11-25/server/tools#user-interaction-model)
- 2025-11-25 版 Tasks 被官方标为实验性机制。双方通过初始化能力协商，再结合工具 `execution.taskSupport` 的 required/optional/forbidden 判断是否可用；任务有独立状态查询、结果取得及可选取消操作。不能因服务使用 MCP 就假定支持异步 Tasks。[MCP Tasks](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks)
- PSKit 的 Pending(job_id) + status_tool 是应用层异步协议，不等于原生 MCP Tasks。建议先确认 CORAL 的真实模式，再选择现有 job adapter 或增加 Tasks adapter；人工审核与验收记录仍属于 PSKit 管理流程。[mcp_adapter.py:41](../../new_backend/pskit_compute/mcp_adapter.py#L41)、[MCP Tasks 能力协商](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks#capability-negotiation)

### 建议的最小接入闭环

以下是后续设计输入，不是本轮执行计划或完成声明：

1. 输入 URL，完成传输/鉴权探测、initialize 与完整 tools/list，保存原始发现快照和 digest。
2. 从工具选择创建能力草稿；协议能发现的字段自动填写，版本、预算、执行/结果映射及领域 UI 提示采用服务声明或管理员配置。
3. 将验收案例版本与服务 revision、schema digest、执行绑定及结果断言绑定；运行后存完整状态、用量、产物与断言结果。
4. 发布前展示真实验收证据、工具调用范围、资源策略与 UI 预览；人工批准明确的版本，修改后重新验收。
5. 发布原子切换能力可见性与执行绑定，确认通用 worker 可用；新作业使用已发布版本，已有作业保留快照。

本轮仅检查源码与官方文档；未执行 CORAL/GPU 请求、未运行测试、未修改业务代码、未部署。

## 4090 远端 CORAL 核对

2026-10-06 通过 Windows SSH 配置中的 `jhli-4090-wg` 只读访问，主机报告 `szu-ESC8000A-E11`。以下路径均为远端路径，非本机 checkout：

- `/data/jhli/project/CORAL/mcp_server.py`
- `/data/jhli/project/annoy-coral/tmp/mcp_server.py`

两文件均 192 行，SHA256 相同：`8fa934d73cd148cbac593981330d7fa1f1fa7827d35b2c9c7d2c1e8807595b43`。优先以 CORAL 根目录版本作为服务化入口；另一个位于分析项目 tmp 中，不能默认当成独立、已打包的服务。CORAL checkout HEAD 是 `493d1603028f1b0ac27456e46a42441adc217250`，`config/finetune_flow.yaml` 有本地改动，不能仅凭 commit 声称配置可复现。

### 实际提供的接口

```python
generate_rna_for_protein(pdb_id: str, chain: str,
                         num_samples: int = 1, length: int = 50)
```

`mcp_server.py:133–192` 使用 FastMCP，服务名 RNA-Design-Expert，启动为 `transport="sse", host="0.0.0.0", port=8099`。当前 `ss -ltn` 未发现 8099 监听，所以只能确定源码存在，尚不能给出可用 MCP URL。旧 SSE 通常以 `/sse` 为入口，但此次没有运行服务确认实际路由；未来建议提供 Streamable HTTP `/mcp` 供 PSKit 接入。

输入仅支持 PDB ID 和链，尚不支持任意纯蛋白质序列或结构上传。校验数量 1–10、长度 10–500；docstring 将长度写成 10–200，存在漂移。正常结果为：

```json
{
  "status": "success",
  "input": {"pdb_id": "...", "chain": "...", "protein_sequence_preview": "..."},
  "generated_rnas": [{"id": "...", "sequence": "ACGU...", "length": 50}]
}
```

这不符合当前 `Completed | Pending | Failed` 契约，尤其没有 usage。PSKit CORAL 结果解析已能识别 `generated_rnas`，但输入工具名、能力 ID、封装与执行链仍需适配，不能把“前端能解析数组”视为接通。

### 服务化前需要处理的具体问题

1. **校验必须早于模型加载。** `mcp_server.py:154–167` 先初始化全套模型，再检查输入；非法请求也可能触发冷启动。把范围、PDB 标识、链名检查放到 Pydantic 参数模型中，公开到 inputSchema。
2. **不要阻塞控制接口。** async 工具直接调用同步 `requests.get`、Foldseek 和 CUDA 推理；下载未指定超时。独立有界执行队列处理推理，让状态、取消和健康接口在运行期间保持响应。
3. **修复输入到 shell 的路径。** PDB ID 只有长度检查，进入缓存路径；`foldseek_clstr.py:173–178` 用字符串拼接后 `os.system` 执行。严格校验标识、隔离作业目录，改用 argv subprocess 并检查返回码和超时。
4. **缓存和并发。** 当前共享 `tmp/{pdb_id}.cif`，下载直接写最终路径，缺少原子写入和并发保护；模型第一次初始化也没有显式锁。按设备配置并发上限，先以单 GPU 一个运行槽验证。
5. **区分真实蛋白序列与结构编码。** `foldseek_clstr.py:234–240` 返回 `parsed_seqs[2]` 的 combined_seq；工具将它标为 protein_sequence_preview。结果应明确命名结构条件编码，并另取真实氨基酸序列。
6. **返回错误与用量。** 当前失败是 `{error: string}`；需要稳定错误码、已发生用量及终态。不能把缺失 GPU 用量填成零，也不能将网络等待时间直接算成 GPU 时间。
7. **固定环境与版本。** 模型包 `model/`、`flow_utils/` 及相应类存在；推理权重、对齐权重和两个 `/home2/jhli/model_checkpoints/...` 目录当前可读。非交互 shell 没找到 foldseek，环境文件没发现 fastmcp 声明；这不证明 Conda 环境缺失，但仍需在选定环境中核实并锁版本。此次没有 import 重型模块、启动进程或加载权重。

## 建议的产品接入流程

### 对同门：一次适配，之后填地址

保留 MCP 作为标准传输，增加版本化的 PSKit Compute Profile。服务通过标准 MCP resource（建议 URI `pskit://manifest`）公开 manifest，或由管理员上传等价配置；这是待实现的扩展，不是 MCP 自带字段。

manifest 需声明：服务/模型版本、能力 ID、提交/查询/取消工具绑定、业务结果 schema、用量单位及来源、预算/取消能力、UI 展示提示和验收样例。完整 wire outputSchema 与内部 result_schema 必须分别存储。凭据单独存服务端，不能放入公开 manifest。

长期任务建议先沿用既有 PSKit 作业机制：

```text
阿里云通用计算执行器 → 4090 MCP submit → pending(job_id)
                     → status(job_id) → completed / failed + usage + artifacts
                     → PSKit 持久化/结算 → 唤醒 Pi
```

PSKit Job 是用户侧任务与记账事实源；4090 的外部 job_id 只标识远端执行。提交必须携带稳定 execution key，绑定 PSKit job/attempt；超时重试时可查回原作业。当前 McpAdapter 只传业务 arguments，尚需增加受信任的执行元数据通道，不能要求模型或浏览器自行填写 user_id/budget。

4090 计算服务应保存作业状态、结果和待确认记录；PSKit 成功持久化后返回提交确认，再允许服务清理结果，或使用明确且足够长的保留策略。普通 MCP 通信并不自动提供此确认语义。进度由实际阶段/步数产生；远端取消返回“收到请求”不等于 GPU 已停止。

低频小任务可直接 completed。长任务的界面断开、模型 turn 结束不应取消远端工作。需要按用户配额先预留预算，终态结算实际资源；对独占 GPU 的执行区间可计 GPU 占用毫秒，CPU 计进程/作业累计 core-ms。共享 GPU 无法仅靠函数墙钟获得可靠分摊值，必须服务报告口径；只有具备可确认停止的独立执行边界才能宣传硬限额。

### 对管理员：五步接入向导

1. **连接：** MCP URL、可选凭据。检查阿里云后端到地址的连通性，而不是浏览器或 SSH 是否能连。内网地址由管理员纳入允许范围；远端声明不会自动扩展网络权限。
2. **发现：** 展示工具列表，选择公开能力，读取 manifest；自动填写参数与结果配置。缺少版本、计量或异步接口时显示具体缺口，允许补充受限配置。
3. **验收：** 平台固定协议用例 + 提供者科学样例，分别显示待运行、运行中、通过、失败、未执行。GPU 样例显示预估预算并通过受限验收账号执行。
4. **预览：** 左侧自动生成输入表单，右侧预览进度、结果、下载与“交给 Agent 分析”入口；配置中文/英文名称、图标、字段分组和输出展示组件。
5. **审核发布：** 管理员查看当前版本全部必要案例，批准精确 revision/digest。只有执行器配置就绪才变为可调用；修改工具/schema/映射或声明版本会使旧验收失效，需要重验。保存审计与上一发布版本。

远端内容可能不经过版本声明就变更，schema digest 本身也不能验证权重；发布记录应包含服务版本/镜像 digest/权重标识和维护者保证，运行时对不匹配的声明暂停接入并要求重验。

## 推荐验收案例（尚未执行）

服务作者样例只提供输入和声明式断言，不能通过 manifest 上传任意 Python/JS 由 PSKit 执行。平台内置检查不可被服务自行标记跳过或通过。

| 案例 | 输入或操作 | 必须满足 |
| --- | --- | --- |
| 协议发现 | initialize、完整 tools/list、manifest | schema 可解析，工具绑定存在，输出封装/result schema 层次一致，范围和单位明确 |
| 最小成功 | 固定并缓存的有效 PDB/链，num_samples=1、length=50 | 成功返回 1 条 RNA，长度与声明一致、字符在声明字母表内，结果通过 schema，GPU/CPU 用量按策略完整；保存全部证据 |
| 批量成功 | 同一 fixture，num_samples=2、length=100 | 正好返回 2 条、每条 100 位；不要求两条一定不同，也不以随机序列精确相等作为断言 |
| 输入拒绝 | 数量 0/11；长度 9/501；非法 PDB 字符/链 | 调用前或模型加载前明确拒绝，不排队占 GPU；实际零消耗需有执行证据 |
| 数据错误 | 合法 PDB、不存在的链；模拟下载超时 | 稳定错误码，任务终态可查询，已消耗资源不丢失，控制接口仍可用 |
| 异步恢复 | submit 返回 pending 后断开连接，再查状态 | job_id 稳定，最终能获得结果/产物；正常运行不依赖浏览器保持连接 |
| 重复提交 | 同一 execution key 提交两次；丢失首次响应后重试 | 返回同一任务，仅执行一次、结算一次 |
| 预算与并发 | 超出预算的请求；超过运行槽的并发请求 | 超额拒绝/排队，预留用量不重复；已消耗任务不可被误记为零 |
| 取消 | 排队取消、运行中取消分别测试 | 区分请求取消与确认停止；实际停止后释放资源，结算已使用部分；不支持取消需明确受限 |
| 结果确认 | 断开回传连接、重复终态报告 | 服务保留未确认结果；PSKit 重收不重复计费，确认后才能清理对应记录 |
| 可选产物 | FASTA/CSV 文件下载 | 文件完整、所属用户可访问、他人不可读；返回数量与文件记录数对应 |

CORAL 源码 demo 使用 `6FXB_D`（foldseek_clstr.py:243）；可作为 fixture 候选，但必须先核对结构链和适用条件再固定。`1A8W_A` 只是工具 docstring 示例，本轮没有确认有效性。上述功能验收不证明候选 RNA 对目标具有真实亲和力；科学有效性需要额外的已知基准与实验标准。

当前脚本不支持异步、幂等、预算、取消和确认；它不会通过完整验收。第一轮只读检查已完成，GPU 样例尚未执行，不能在管理台标成 passed。

## 前端如何做到配置式上架

面向用户继续采用现有 200×160 工具卡片，搜索后点击进入占满 Tools 内容区的工作台，左上角返回。工具目录从已发布能力生成，不再为每个新模型增加路由和条件分支。

工作台统一为输入区、任务状态/真实进度区和结果区，历史按能力与用户过滤。inputSchema 负责数据约束，单独的 UI 配置负责字段顺序、标签、帮助、组件和结果字段映射。例如 CORAL 可以声明 PDB ID + chain 输入、数量/长度整数输入、RNA 序列表格结果和 FASTA 下载，均使用平台预置组件。

建议首批通用组件：基础文本/数字/枚举/布尔；序列编辑器；受控文件上传；PDB/链选择；结果表格；FASTA/CSV 下载；结构查看；可选图表。对于已有异步 Job，复用通用进度和任务历史；对无进度的服务只显示真实 running 状态。全量结果以产物持久化，交给 Agent 时附输入快照、摘要和文件引用。

“零代码”适用于平台已有控件和协议覆盖的模型：同门提供一次标准服务，管理员配参数和展示，用户页面自动生成。全新输入/结果类型需要平台补一个可复用组件；不能承诺任意科学可视化只靠 JSON Schema 自动得到理想界面。

“接入已有服务”和“部署模型进程”应拆成两个动作。只填 URL 能连接已经运行且可达的服务，不能从地址推出镜像、权重和 GPU 环境。若以后要一键部署，再接受固定 image digest、权重挂载、GPU/内存需求、健康检查、端口、环境变量引用等部署清单，在预先登记的 4090/A6000 节点启动容器；部署成功得到 URL 后进入同一个验收流程。

推荐下一阶段先完成：CORAL 标准适配 → 自动发现及工具绑定 → 版本化验收/审核 → 通用工作台，跑通一个模型后，再推广到同门其它模型及部署模板。这是讨论建议；本轮没有新增业务代码或改变远端服务。
