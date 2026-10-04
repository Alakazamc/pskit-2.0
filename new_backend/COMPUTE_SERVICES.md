# 科研模型接入：统一返回协议与薄 SDK

本轮实现通用计算（B 阶段）。每用户沙箱完善和管理台（A/C）仍由独立计划交付。此改动尚未发布生产，也没有把旧后端的所有模型权重自动迁过来。

## 1. 维护者负责什么

保留原模型环境、权重、启动方式和用量采集。把函数或已有 HTTP/MCP 的结果适配为 `ExecutionReport`，提交服务 manifest 和实际成功/失败/Pending 样例。SDK 只负责输入 Schema、可信执行上下文、返回校验和接收器持久化，**不自动测量远程 CPU/GPU**。

当前 SDK 随 Python 后端包发布：在模型环境中安装 `new_backend/` 或其 wheel，然后 `from pskit_compute import ...`。它不是独立发布到 PyPI 的轻量依赖包；独立分包可后续完成。支持 Python 3.12+，MCP 保持项目 `>=1.26,<2`。

```python
from pskit_compute import Completed, ComputeBudget, ComputeService, UsageReport

service = ComputeService("lab-rna", "weights-v1")

@service.compute_tool(name="predict", version="1", gpu_count=1,
                      required_usage=["wall_ms", "gpu_device_ms"],
                      max_budget=ComputeBudget(gpu_device_ms=120000))
async def predict(sequence: str):
    response = await existing_model_service.predict(sequence)
    return Completed(result=response.output, artifacts=response.artifacts,
        usage=UsageReport(wall_ms=response.wall_ms,
                          gpu_device_ms=response.gpu_device_ms,
                          source="service_reported"))
```

模型函数需要领域输入的类型注解。可选 `ctx: ExecutionContext` 不进入工具输入 Schema；owner/run/grant 等身份由平台绑定。同步函数在线程执行，异步函数正常 await，不要求改模型框架。`new_backend/examples/compute_service.py` 展示包含已消耗用量的失败处理。

`service.manifest()` 生成草稿。运营人员检查并通过服务器配置注册已批准版本；用户不能注册或改写服务版本。版本不可原地更改，输入/输出/策略变化要增加 version。`service.validate(capability_id, report)` 提供与后端相同的返回验收检查，后续管理台 Test 可复用；本轮没有新管理页面。

## 2. 返回协议

| 返回 | 必需字段 | 语义 |
| --- | --- | --- |
| Completed | status=completed、result 对象、usage | 执行结束；artifacts 为可选授权引用 |
| Pending | status=pending、job_id | 服务已提交异步任务，尚无最终 usage |
| Failed | status=failed、error(code/message)、usage | 失败，但已消耗资源仍结算 |

异步最终报告必须携带原服务 `job_id`。不把 Pending 当作 MCP 未完成的 tool call，不依赖 MCP Tasks 扩展。平台作业 `compute-...` 与服务作业 job_id 是两个关联 ID。

UsageReport 的六个数值字段可为 null：`wall_ms / cpu_core_ms / gpu_device_ms / peak_memory_bytes / peak_gpu_memory_bytes / gpu_count`。均严格非负整数，拒绝小数、布尔和数字字符串；null 是未知，0 是已知无消耗。CPU 是全部核累计毫秒；GPU 是全部设备累计毫秒；内存/显存为峰值字节。暂不计算显存 byte-ms。

`source` 必填：`service_reported / measured / estimated / unknown`。维护者自己负责归因；内部服务可以经批准自报，平台保留来源，不能将它描述为独立核验的账单。服务指定 required_usage 和 accepted_sources；必需值为空、单位错误、结果不匹配 output_schema 会被拒绝。无可用指标的异常保留 unknown 并等待对账，**不按零结算或释放预占**。有部分用量时抛 `ComputeFailure(code=..., message=..., usage=...)`。

## 3. 服务注册与配置

先使用管理员数据库连接显式运行 `app.db.postgres_migrations.migrate_postgres` 升级至 v4（004_compute.sql）。启动只验证版本，不静默迁移。应用 role 的新表/序列权限按现有 shared-Postgres provisioning 授予，不授予用户/模型数据库权限。

在后端专用 env 文件（0600）配置：

```dotenv
RESEARCH_AGENT_COMPUTE_ENABLED=true
# CPU 是核毫秒/UTC 日，缺省 0；必须由运营人员根据资源容量设置。
RESEARCH_AGENT_COMPUTE_CPU_DAILY_LIMIT_MS=0
# 服务器受控 JSON 数组，包含审核后的完整 manifest（visibility=published）。
RESEARCH_AGENT_COMPUTE_SERVICES_JSON=[]
# 服务独立随机密钥；禁止发到前端、Pi 环境或聊天。
RESEARCH_AGENT_COMPUTE_SERVICE_KEYS_JSON={}
```

需 live PostgreSQL 与 persistent Pi runtime。没有 manifest 的目录为空，不产生演示模型。当前 manifest 由运维配置导入，管理台发布/撤销/用户分组在 C 阶段接入。GPU 沿用现有用户每日分钟限制，内部乘 60000；CPU 可用服务器默认或 compute_cpu_limits 的用户限额。已有 Token 月账本保持不变。

## 4. 用户、Pi 与 worker 接口

| 调用方 | 路径 | 授权 |
| --- | --- | --- |
| 用户 | GET /api/v1/compute/capabilities | Python 验证用户 JWT、发布和用户 allowlist |
| 用户 | POST /api/v1/compute/jobs | JWT + Idempotency-Key；输入只有 capability_id/version/arguments/budget |
| 用户 | GET /api/v1/compute/jobs/{id}、POST .../{id}/cancel | ownership；跨用户 404 |
| 用户 | GET /api/v1/compute/usage | CPU/GPU used/reserved/remaining，整数毫秒与来源 |
| Pi | POST /internal/compute/jobs、GET .../{id}?run_id=... | Run 范围工具 token；服务器绑定 owner，绝不接收 user_id |
| worker | POST /internal/compute/jobs/claim | service_id、worker_id、GPU UUID；X-Compute-Key |
| worker | POST .../{id}/heartbeat、POST .../{id}/result | X-Compute-Service + X-Compute-Key + attempt/fencing_token |

结果回执包含 receipt_id、job_id、accepted_seq、payload_hash、status、committed=true，事务提交后才返回。同 seq 同 payload 重发返回原回执；不同 payload 409。心跳用量是累计值，终态也是完整累计值。显式 UsageWindow 使用时区明确的 start/end；按窗口比例做 UTC 日归账，余数给最后一天，这只是归账政策。未给窗口时按预占日归账，不伪称逐日物理采样。跨日未结算 hold 不消失。

## 5. 长任务与接收器

`pskit_compute.receiver.Receiver` 配合 SQLite WAL journal：开始执行前写 executing；完成报告先写 outbox 再发中央。只有匹配的已提交回执才能删除。Pending 回执保留 waiting 记录，继续查询原外部任务。重启后 outbox 重发；executing 或丢失本地记录的恢复 claim 标 unknown，避免再次推理，需运营人员核实实际服务任务。

`HttpAdapter` 配置 submit_url/status_url/cancel_url 和可选数据转换函数；submit 发平台 Job ID 作为 Idempotency-Key。`McpAdapter` 使用现有 Streamable HTTP ClientSession，配置提交/查询/取消工具，解析 structuredContent 或单个 JSON text block；MCP isError 的 Failed 报告也保留 usage。函数返回 Pending 时应提供有 poll/cancel 的 executor，不能自动猜测外部状态。

在模型自己的环境启动（密钥只在环境/0600 env 文件中）：

```bash
python scripts/compute_receiver.py \
  --backend-url http://private-backend:8000 \
  --service-id lab-rna --worker-id lab-rna-worker-1 \
  --executor your_module:service \
  --journal /persistent/pskit-compute/receiver.sqlite3 \
  --gpu-uuid GPU-actual-device-uuid
```

`PSKIT_COMPUTE_SERVICE_KEY` 由服务维护者在运行环境设置。journal 目录必须持久卷，文件权限 0600，独占文件锁禁止两实例同时执行同一 journal。执行未知时接收器停止并保留记录；网络错误重试回传，不重试模型执行。

## 6. 预算、停止和兼容边界

- 额度预占限制准入，soft 服务实际超过预算仍如实入账，不截断报告。deadline/lease/取消请求不是远程 CUDA 已停止的证据。
- grant stop_at 固定，心跳不延长。到期通知 cooperative cancellation；硬限制只允许声明 independently stoppable + confirmed_stop 的真实 executor，本轮没有实现通用 supervisor。
- running 取消先 cancelling；终态报告必须显式 stopped=true 才释放余量和设备锁。失联/未知仍保留锁与预占，不自动重跑。
- 旧 AF3 receiver/API 保留原启动环境和分钟计量，来源 legacy_wall；旧任务和新任务共享用户预算，但不互相当成同一种 wire model。
- **通用 GPU UUID 锁尚未覆盖旧 AF3 receiver 的物理设备锁。** 同一 GPU 接新通用 executor 前需排空旧 executor，或将它适配到同一通用 claim/receipt 流程，不能并行部署后宣称跨旧新 receiver 硬件互斥。
- Pi 已支持 submit_compute → terminate → 后台终态 → 原会话自定义事件继续。事件不是用户自然语言消息；独立工具页 Job 不创建聊天。若用户主动要求 LLM 分析，用已有“交给 Agent 分析”/消息接口创建 Run，走受控模型代理和 Token 账本。
- 本轮产物为 ArtifactRef；SDK 不自动上传任意服务器路径。旧 AF3 blob 下载链路保持，通用模型产物存储上传适配需要具体服务对接。

## 7. 可复现验收

使用现有 `deploy/agent/tests/compose.pg17.test.yaml` 的固定镜像 `supabase/postgres:17.6.1.136`，独立临时测试 schema 自动清理。按 `new_backend/` 工作目录运行：

```bash
TEST_POSTGRES_DSN='postgresql://postgres:pskit-test-only@127.0.0.1:15433/postgres' \
  .venv/bin/python -m pytest tests/postgres/test_compute* tests/test_compute* -q
```

也可运行 `python deploy/agent/scripts/compute_smoke.py`（从仓库根目录；仍需 TEST_POSTGRES_DSN）。它执行实际本地 CPU 函数、HTTP 控制、中央 PostgreSQL、丢 ACK 重启、配额拒绝和 Pi 路径测试。CPU 示例只在测试 schema；不向生产导入测试数据。真实 Pi RPC 使用本地替身模型，无付费 LLM 调用；GPU 只验协议，不宣称 A6000/CUDA 计量或硬停止已验收。

协议文件：`contracts/openapi.json`、`contracts/compute.schema.json`；前端类型从同一 Python schema 生成于 `new_frontend/src/api/generated-compute.ts`。
