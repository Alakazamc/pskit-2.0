# CORAL 输入面板与模型服务接入

## 当前交付

工具目录新增 CORAL 小卡片，规范入口 `/tools/coral`。使用现有通用计算队列，不增加专用科研工作流。界面包含蛋白质输入、三个任务节点、序列预览、FASTA 下载、独立运行历史与 Agent 交接菜单。

本次没有部署 CORAL 模型或提交真实 GPU 任务。后端未开放对应能力时，面板会提示未连接并禁止运行。截图和测试中的数据来自接口替身。

## 输入边界

旧实现的事实依据：

- `backend/app/tasks/validation.py` 的 `CoralInput` 接收四位 PDB 编号、蛋白质链、候选数量和 RNA 长度；原数量上限 100，长度上限 200。
- `backend/app/tasks/worker.py` 的 `call_remote_rna_expert` 调用远程 MCP `generate_rna_for_protein`，参数为 `pdb_id / chain / num_samples / length`。

首版面板沿用 PDB + 链，不将任意蛋白质序列或文件假装成已支持的输入。规范能力 ID 为 `coral.generate_rna`。数量/长度上下限来自发布版本的 `input_schema`。默认数量为服务上限与 10000 的较小值，默认长度为服务上限与 100 的较小值，并遵守 minimum。因此“100 长度、1 万条”需要接入服务实际声明并支持该范围，旧接口不会自动获得这一能力。

建议基于薄 SDK 在模型环境中定义 `ComputeService("coral", pinned_model_version)` 和 `@service.compute_tool(name="generate_rna", ...)`，使用 `Annotated[int, Field(ge=1, le=actual_limit)]` 声明真实范围。审核后在管理台发布生成的 manifest；也可使用服务器受控 `RESEARCH_AGENT_COMPUTE_SERVICES_JSON`。具体配置、接收器启动、用量来源与取消语义见 [COMPUTE_SERVICES.md](COMPUTE_SERVICES.md)。平台不会从模型名字推断服务地址。

## 请求与进度

```json
{
  "capability_id": "coral.generate_rna",
  "version": "1",
  "arguments": {"pdb_id": "1A9N", "chain": "A", "num_samples": 100, "length": 100},
  "budget": {"cpu_core_ms": 0, "gpu_device_ms": 120000}
}
```

由前端 API Client 向 `POST /api/v1/compute/jobs` 发送 JWT 和 `Idempotency-Key`。示例预算仅示意，实际取服务公布的最大预算并经过后端每日额度预占。相同输入提交失败后重试复用 key，避免通信失败造成重复任务；显式重新生成是新任务。

三个节点表示平台可观测状态：

1. 输入确认：服务器接受并持久化任务后完成。
2. 序列生成：排队时等待，worker 运行时激活；百分比来自真实 heartbeat。
3. 结果就绪：仅在任务和报告均为 Completed 时完成。

每秒读取活动任务快照；终态或网络错误时停止轮询。刷新和历史恢复使用 URL `?job=...`。取消已运行任务先显示“正在停止”，等待服务确认，不把请求成功当作 GPU 已停止。未知内部科学阶段不会被模拟成动画节点。

## 输出契约

推荐 Completed 的 `result` 使用以下字段，数量和长度都是实际结果：

```json
{
  "status": "completed",
  "result": {
    "sequence_count": 3,
    "sequence_length": 4,
    "candidates": [
      {"id": "rna-1", "sequence": "ACGU"},
      {"id": "rna-2", "sequence": "UUAG"},
      {"id": "rna-3", "sequence": "GCAU"}
    ]
  },
  "usage": {"source": "service_reported", "wall_ms": 8000, "gpu_device_ms": 6000},
  "artifacts": []
}
```

兼容 `sequences / generated_rnas` 字符串数组以及对象的 `rna_sequence`。只预览前五条；完整数组长度取实际返回值，缺失或无效 RNA 字符会标记数据不完整。不会将请求的 10000 当作实际完成数量。不同长度显示范围；部分数据有明确提示。原始 JSON 按需展开，初始不把整批数据渲染到 DOM。

若只返回少量预览，必须附上真实 `sequence_count`；完整数据使用有权限的 ArtifactRef，不接收任意下载 URL。界面会明确预览局限，禁止将预览导出为“完整 FASTA”。已有 MCP adapter 的报告上限为 1 MiB，超大结果应采用产物引用或经过审核的 HTTP/SDK 接收器，不直接塞进该 MCP 响应。当前 Agent 交接对完整内联数组自动生成附件；仅有产物引用时不会自动下载并解析它，提示词会保留产物 ID 和数据不完整状态。

## 新对话交接

“交给 Agent”提供分析序列、筛选候选、设计验证方案。用户选定任务后：

1. 使用该任务的 **job.arguments 快照**，不读取之后编辑的输入框。
2. 完整数据生成 FASTA，记录按返回顺序命名 `candidate_1 ... candidate_N`；上传块小于 900000 字节，每轮最多十个文件。10000 × 100 测试数据为两个块，单独下载时合并为一个 FASTA。
3. 上传成功后创建个人 Session，启用首次回复后自动命名。
4. 发出固定任务说明、输入/结果摘要、五条预览、任务 ID、版本、用量来源与文件 ID。摘要 JSON 最多 12000 字符；完整文件由既有 Pi 工作区同步机制提供给 Agent。
5. 消息被接受后导航 `/session/:sessionId`，由正常 Run/事件流开始执行。

上传失败不会提前创建空会话。同一任务重试保留已上传的附件、Session ID 和消息幂等 key，避免重复发起聊天。文件和 Token/GPU 额度仍由后端执行。固定分析说明不预设生成序列已具有结合亲和力。

## 运行历史接口

新增 `GET /api/v1/compute/jobs?capability_id=coral.generate_rna&limit=30`。用户身份由 JWT 绑定，精确过滤能力 ID，默认最近 30 条、最多 50 条；返回 ID、能力版本、输入、状态、进度、创建时间，不返回大体积报告和 worker 细节。打开一条历史后再读取已有 owned Job 接口。跨用户任务仍为 404。接口复用既有 PostgreSQL 表，无新迁移。
