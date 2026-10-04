# 通用计算与薄 SDK 交付记录

## 已实现

用户提供的统一返回协议已写入总计划和 B 分计划，并在当前工作区执行。平台校验 Completed/Pending/Failed 与维护者上报的 UsageReport；不假设所有服务位于同一硬件，也不通过请求等待时间捏造 GPU 消耗。

- PostgreSQL v4：不可变服务版本、持久 Job、owner、幂等请求与策略快照。
- 整数 CPU/GPU 预占和累计用量：UTC 归账、来源、失败消费、跨日未结算 hold、旧 AF3 配额兼容聚合。
- 服务认证与 worker：attempt/fencing、固定 deadline、通用 GPU UUID 锁、取消意图和确认、失联待对账。
- 结果/账本/receipt/outbox 同事务；本地 WAL journal 先持久报告，丢 ACK 后仅重发，匹配提交回执后清理。
- Python 函数薄 SDK、HTTP/MCP 1.x adapter；Pending 查询原服务任务，不重新提交推理。
- submit_compute 停止 Pi；终态以自定义事件恢复原会话，走原 Run lease 与 Token 账本。独立 Job 不创建聊天。
- OpenAPI、JSON Schema、前端生成类型、维护者接入文档与隔离验收脚本。

## 测试证据

从 new_backend/ 执行，TEST_POSTGRES_DSN 指向现有固定镜像 `supabase/postgres:17.6.1.136` 的本地测试实例；每项 PostgreSQL 测试使用临时 schema，结束自动清理。未向生产导入测试数据。

| 检查 | 实际结果 |
| --- | --- |
| 各任务边界验证 | 19、11、13、9、10、34 项；第 7 项综合验证 50 项通过 |
| 全后端回归（终审修复后） | 503 passed，0 skipped，102.07 秒 |
| 迁移边界 | 9 项通过；计算状态存在时拒绝有损 SQLite 回退导出 |
| Python ruff、git diff --check | 通过 |
| 前端 typecheck、lint、build | 通过；保留已有 bundle 提示 |
| SDK wheel | uv build --wheel 通过，包含 SDK 与 migration 004 |
| 实际本地 CPU / HTTP / PG / 丢 ACK 重启 | 推理只执行一次，报告重发取得原 receipt |
| 实际安装的 Pi RPC | 使用本地模型响应替身；停止与原 session 恢复通过，无付费调用 |

全套命令：

```bash
PYTHONPATH=.:.. TEST_POSTGRES_DSN='<local-test-DSN>' \
  .venv/bin/python -m pytest -q --tb=short
```

有 1 条已有 Starlette TestClient/httpx 弃用警告。没有以跳过 PostgreSQL 测试或隐藏警告宣称通过。GPU 仅验证协议，未运行 A6000/CUDA 推理、计量或硬停止；未部署到生产。

## 最终审查与修复

一次独立只读审查覆盖 `8ebecde..e14a085`，没有 Critical，确认 4 项 Important。一次 TDD 修复批次分别复现失败后修复：

1. 旧 AF3 跨日 hold 漏算：reserved/pending_reconciliation 两态均保留准入占用。
2. 远程 GPU 预算可用未知用量释放：非零预算必须声明必需指标；远程 GPU 同样要求正预占，缺少用量保留 hold。
3. SDK 参数类型被 model_dump 破坏：函数获得校验后的 BaseModel 与容器字段对象。
4. 完全失联的通用 worker 不标待对账：新增自动扫描，保留 fencing/设备/预占，接受原 attempt 迟到报告。

完整回归另外发现 v4 与旧 SQLite 迁移表集合不兼容；新增有损回退拒绝测试，允许旧数据导入新增计算表为空的 v4 schema，禁止静默丢失计算数据。全部修复后全后端 503 项通过，没有追加第二轮审查。

## 执行裁定

1. **实际迁移用 004**：A 的迁移尚不存在，不创建空编号。若后续顺序改变，A/C 需重排迁移版本。
2. **复用 Run scheduler/outbox、旧 AF3 来源聚合**：避免重复计账与第二套调度。旧 AF3 物理锁仍未与通用 UUID 锁合并，同设备切换前必须排空。
3. **按真实 HTTP/PG/SDK 边界验证**：修正计划占位签名/测试名，Pi 模型用本地替身，避免付费调用。它不证明实际远程模型、CUDA 硬停止或生产部署。
4. **旧直接 MCP invoke 保持 completed**：长任务三态走 McpAdapter/receiver/submit_compute，复用一种持久机制。现有直接 MCP 长任务服务需改接通用路径。

## 暂缓的小项与接入限制

审查的 1 项 Minor：内部 extend helper 没有能力最大预算检查与 Job/grant 快照同步；目前没有调用方或公开接口，启用前必须补齐。

SDK 随后端 wheel 分发，尚非独立 PyPI 包；管理台发布/Test 在 C 接入。通用 ArtifactRef 不自动上传服务器文件。服务自报用量是维护者声明，不是独立核验计费。真实模型环境、权重、异步状态/取消与硬限制能力由维护者提供，不能从示例推断全部模型已接入。

接入说明：[COMPUTE_SERVICES.md](/home/jhli/pskit-2.0/new_backend/COMPUTE_SERVICES.md)。
