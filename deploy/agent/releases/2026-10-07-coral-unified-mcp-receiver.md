# 2026-10-07 CORAL 开放与统一 A6000 MCP 接收器

用户要求恢复生产 CORAL，并把 AF3 接收器并入 A6000 通用 MCP 接收器。生产入口为 <https://agent.bioailab.net/tools/coral>。本次没有重建 React、迁移数据库或修改宿主 Nginx。

## 实际拓扑与制品

| 位置 | 当前运行内容 |
| --- | --- |
| 阿里云 | 原 Python/Pi、React dist、Supabase、PostgreSQL、LiteLLM；AF3 私网代理与新增通用私网 Caddy |
| A6000 | `pskit-mcp-receiver-a6000` 一个容器、一个 Python 进程，两个接收循环；独立 AF3 模型计算容器保持运行 |
| 4090 | CORAL MCP 四种能力；既有 Redis pocket 计算队列和消费者保持原状 |

接收器源码提交 `aed2631abb3d432c78d0b7ff38384069ddc4187a`；归档 SHA256 `94b1bbe447a7c7d67157c9c2eed8b2fea1e9cd22ed017472b50c085cf55ed62b`。源码只读挂载于 `/app`，运行目录为 `/data/jhli/pskit-mcp-receiver-20261007/runtime-aed2631`。缓存依赖镜像固定为 `sha256:67d5aa962921e08be408ebce0852bca6e86afaabbb39291634dcbb5bb59dcece`，已核对 MCP、httpx、Pydantic、jsonschema、uvicorn，并在隔离容器运行真实 Streamable HTTP MCP 验收。此处使用固定依赖镜像与独立源码制品，不能宣称镜像本身内嵌本次源码。

后端镜像保持 `sha256:03107b7953e5e214b624233265b07cba1dd7df039a6c03323ed1aa9d4f071282`。AF3 私网代理源码单独固定为提交 `4febde9`，SHA256 `8816ac8994133aeb457665b7c8e3dd888f1aabe4e43b2b215d1273af41e40fe0`，由 `AGENT_AF3_PROXY_SCRIPT` 只读挂载。原 AF3 计算容器 ID 与启动时间保持不变。

## CORAL 生产开放

用户明确授权：在 4090 本机读取 `/data/jhli/project/annoy-coral/run_evolutionary_sample_iptm_dynamic.py` 中已有 Redis 配置，用于同机 MCP 私有配置。密码没有导出到 WSL、聊天或提交；运行环境文件权限为 `0600`。隔离目录补充 `openpyxl==3.1.5`、`et-xmlfile==2.0.0`，未修改共享 Conda 环境。

四个重新执行的生产验收案例通过：一次生成、迭代生成、蛋白质 pocket 分析、2D 优化。2D 案例使用固定序列夹具并设置 `skip_structure=true`；不代表所有结构分析路径已经验证。没有复制 Staging 账号、数据或审批报告。

| 记录 | ID |
| --- | --- |
| 服务修订 / 产品修订 | `2` / `2` |
| 产品 | `product-coral-scientific-workflows`，slug `coral` |
| 验收报告 | `qualification-89c7877b-685e-4691-bde9-e0074e9780b2` |
| 发布 | `release-7d7f54cd-4d4f-45ec-94dc-4bff475c70e4` |
| 首次用户 API 验收 | `tool-run-7b6fe50a-7ea9-4cbe-a14f-6c2ce1fa22e1` |
| 合并与重启后的用户 API 回归 | `tool-run-1f25a865-ba72-4efb-9cff-f098caf2b07f` |

发布操作记录真实服务器维护身份 `server:ecs-user@aliyun:coral-enable-20261007`；核对已有平台管理员权限，没有伪造 JWT 或新增管理员角色。普通用户 HTTPS 登录、公开目录、四种配置 UI 模式、任务提交、A6000 领取、4090 计算和用量结算通过。最终单样本生成返回 `generated_count=1`、一项产物引用；服务报告 `wall_ms=2545`、`cpu_core_ms=2507`、`gpu_device_ms=2541`、`gpu_count=1`，来源为 `service_reported`。

**独立的产物传输缺口：**通用 MCP 当前保存产物引用，尚未把 CORAL 文件内容送入现有 owned artifact 下载仓库。不能把产物引用数视为 CORAL 文件下载验收，也不能把 `available` 声明视为实际可下载。AF3 的上传和下载路径已单独实际验证。后续需增加服务端受所有权、摘要和大小约束的通用产物传输，并覆盖下载与 Agent 附件交接。

## AF3 合并与恢复证据

统一进程内本地 MCP 仅绑定 `127.0.0.1:18187/mcp`，提供 `af3.submit` / `af3.status`。AF3 使用通用 `Receiver` 和持久 `Journal`；兼容适配器保留旧 HTTP 任务、GPU 结算与会话恢复边界。尚未把历史 AF3 全量迁入通用服务注册表。

切换前服务端没有活动任务，两类 journal 均无未确认记录。旧 `pskit-af3-receiver-local-20261002` 已停止；新接收器 UID/GID 为既有 spool 所有者 `1006:1006`，模型计算容器保持运行。

首个真实 AF3 Job 为 `c6d25ce6-6997-41af-87c1-921e84b82398`。模型完成后，旧代理拒绝新的单任务状态 GET，结果保留在 outbox。TDD 补齐这一条受密钥保护的私网路由、定向更新代理后，原结果继续回传，无需再次推理。任务完成、`simulation=false`、GPU 一分钟结算为 `settled`；19 个 CIF/JSON 产物均通过用户 HTTPS 下载并记录 SHA256。

严格进程恢复验收 Job 为 `89a2a36f-f7fd-4fb2-be20-d6e779444f8a`：以真实 `run_alphafold.py` 命令识别推理 PID `328530`，仅重启统一接收器，耗时 10.47 秒。重启后原推理 PID 仍在运行，计算容器 ID/启动时间与输入文件 mtime 不变。任务随后完成，GPU 一分钟正常结算，19 个产物可下载。早期观察器误匹配短暂健康检查进程的记录不作为 PID 连续性证据。

服务端确认后，本次三个 AF3 验收目录均已清理；通用与 AF3 journal 未确认记录均为零。此前两个测试目录保留，未扩大清理范围。独立代码复核指出的跨服务失败隔离和取消后晚到 GPU 结算问题已修复并有回归测试；CORAL 发生不确定结果不会停止 AF3 心跳与回传。

## 配额、权限与验证

- 受控合成账号使用有上限的临时 GPU/CPU 额度，每次运行结束后按原记录恢复，恢复操作保留审计。原普通用户、管理员配额不变；生产会员 GPU 日额度仍为 `0`。实际使用前需在管理台配置 CPU/GPU 配额。
- 本次没有调用 LLM，不宣称重新进行了 Pi 自动唤醒的真实模型联调；已有协议和恢复测试保留。
- 本地 Ruff 通过；隔离 PostgreSQL、真实 HTTP/MCP socket 与相关接收器回归共 **60 passed**。两个新 Compose 声明校验通过。
- 公网登录页 `200`、未登录 usage `401`、internal `404`、管理 UI/API `403`。WireGuard 管理 UI `200`、未登录管理 API `401`。
- 通用计算入口仅绑定 `10.9.8.1:18186`，非 A6000 来源 `403`；A6000 无密钥的有效 claim 请求 `404`，管理路径 `404`。后端继续验证密钥与执行租约。
- 最终后端 healthy，活动 AF3/通用任务均为零；两类 journal 均无未确认记录。

非秘密验收摘要位于阿里云 `/home/ecs-user/pskit-agent-releases/coral-enable-20261007` 与 A6000 `/data/jhli/pskit-mcp-receiver-20261007`。私有日志与额度快照权限为 `0600`；本文件不含账号密码、Redis 密码、API key、JWT 或回调密钥。

## 恢复边界

A6000 切换前的 Compose 与私有环境备份位于 `/data/jhli/pskit-mcp-receiver-20261007/before-unified`；保留旧接收器容器、旧代码和持久 spool。需要恢复时先停统一接收器，核对两个 journal、服务端 owned Jobs 及已开始模型。不能同时启动旧 AF3 接收器，也不能删除未确认结果。journal UID/GID 已改为既有 AF3 所有者，恢复先前 CORAL-only 制品时须核对它的运行 UID。

AF3 代理旧配置与环境备份位于阿里云版本目录 `proxy-before-4febde9`；代理有未确认结果时不能回退到缺少状态 GET 的旧版本。停新增私网 Caddy前确认没有依赖它的任务。回退应用代码不得抹掉已结算用量、用户历史、产物或发布后写入。
