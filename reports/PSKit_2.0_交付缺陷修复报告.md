# PSKit 2.0 上线交付缺陷修复报告

报告日期：2026-08-22  
交付版本：`0.2.2-delivery`  
线上地址：<https://pskit.bioailab.net>

## 1. 交付结论

PSKit 2.0 已完成生产部署与回归验证。当前 Web、任务 Worker、Qdrant 和科学依赖均通过就绪检查；公开注册、登录会话、Agent 流式对话、RAG、Web 搜索、两个本地小模型、Coral MCP、PepCCD MCP 和 AlphaFold3 均已取得真实成功记录。

本次交付不是只做“端口可访问”或“依赖探针通过”，而是使用生产 API、生产 SQLite、生产队列、生产 Worker、真实 MCP 服务和真实 GPU 容器完成端到端任务，并核对任务终态与产物文件。

## 2. 缺陷与修复清单

| 编号 | 缺陷 | 影响 | 修复 | 验证结果 | 状态 |
|---|---|---|---|---|---|
| 1 | Coral 配置在新版部署中丢失 | Coral 显示为支持，但无法调用 | 从旧部署恢复 Coral SSE 地址，写入受限权限环境文件 | MCP 初始化成功，识别 `RNA-Design-Expert 3.2.0` 与 `generate_rna_for_protein`；真实任务成功 | 已修复 |
| 2 | PepCCD 只有握手记录，没有生成记录 | 无法证明候选生成全链路可用 | 使用生产 API 创建研究运行并提交真实候选任务 | 生成 2 条候选、3 个产物 | 已修复 |
| 3 | AlphaFold3 只有环境探针，没有成功任务 | GPU、数据库、模型、Docker 调度可能在实算时失败 | 提交最小蛋白-RNA复合物任务，完整执行 MSA、模板、GPU 推理、归一化和登记 | 969.231 秒完成，生成 18 个产物，包括 `model.cif` 和置信度文件 | 已修复 |
| 4 | 两个小模型缺乏统一交付回归 | 只有历史单一模型记录 | 分别执行结合位点预测与 PAIR 交互预测 | 26.049 秒/6 个产物；12.743 秒/3 个产物 | 已修复 |
| 5 | Embedding 与 SerpAPI 未配置 | RAG 降级，Web 搜索报错 | 将密钥写入权限为 600 的服务器环境文件；不写入镜像、仓库或报告 | Embedding 返回 1024 维向量；SerpAPI 返回真实结果 | 已修复 |
| 6 | `QDRANT_PATH=""` 被解析为当前目录 | 向量写进 Web 容器临时层，容器重建后索引消失 | 删除空路径变量，强制连接持久化 Qdrant 服务 | Qdrant 持久集合为绿色，53 个 points | 已修复 |
| 7 | RAG 索引依赖人工执行 | 新部署可能一直退化为关键词检索 | 增加一次性 `rag-index` Compose 服务，Web 启动前自动构建索引 | 连续两次重建均自动完成 53 个片段、1024 维索引 | 已修复 |
| 8 | Docker 健康检查请求错误路径 `/api/health/ready` | SPA 回退返回 200，容器被错误标记为健康 | 改为真实 `/api/ready` | 就绪检查包含 database、qdrant、task-worker、science-worker，均为 ok | 已修复 |
| 9 | 正确健康检查会与 Worker 启动条件形成循环依赖 | Web 等 Worker，Worker 又等 Web 健康，冷启动死锁 | Worker 改为等待 Web started；Web 健康继续依赖 Worker 心跳 | 全栈冷重建后 Web/Worker 均自动 healthy | 已修复 |
| 10 | 科学依赖未纳入生产就绪边界 | Coral/PepCCD/AF3 缺失时仍显示 ready | 开启 `SCIENCE_READINESS_REQUIRED` | Coral、PepCCD、Legacy AI、Docker daemon、AF3 image、AF3 GPU 均为 ok | 已修复 |
| 11 | 未知 `/api/*` 被 SPA 伪装成 HTML 200 | 前端把接口错误当成功，监控产生假阳性 | SPA 回退前拦截 API 路径，返回 JSON 404 | 内外网未知 API 均返回 `404 application/json` | 已修复 |
| 12 | 生产 CORS 与安全响应头未正确装配 | 浏览器攻击面和跨域边界不清晰 | 生产关闭开发 CORS，增加 CSP、HSTS、nosniff、DENY、Referrer/Permissions Policy | 公网响应头逐项验证通过 | 已修复 |
| 13 | 公开注册与登录可无限触发密码哈希 | Argon2 参数较重，存在账户枚举与 CPU 消耗风险 | 保留公开注册，同时增加登录/注册请求滑动窗口限制 | 隔离栈第 4 次/分钟注册返回 429 | 已修复 |
| 14 | 会话有效期 14 天且数量无限 | Cookie 泄漏窗口较长，数据库会话持续增长 | TTL 降至 3 天，每用户最多 5 个有效会话，自动清理过期/最旧会话 | 连续登录后数据库会话数稳定为 5 | 已修复 |
| 15 | 普通用户可无限排队科学任务 | 单用户可占满 Worker/GPU | 普通科学任务每用户最多 4 个活跃任务；AF3 每用户最多 1 个 | 第 5 个普通任务和第 2 个 AF3 均返回 422 | 已修复 |
| 16 | 任一工具失败会把整条 Agent 消息标为 failed | 页面显示“消息发送失败”，即使回答和其他工具已成功 | 工具失败保留诊断，但不再等同于消息传输失败 | 不存在的 PDB 请求产生工具错误，但 SSE 200、完整回答、轮次 `succeeded` | 已修复 |
| 17 | Agent 会重复调用相同参数的失败工具 | 浪费外部 API、时间和模型步数 | 记录失败参数指纹，同轮相同调用只执行一次 | 双重相同调用的真实执行计数为 1，第二次标记为已抑制 | 已修复 |
| 18 | RCSB 无结果返回 HTTP 204 时强行解析 JSON | `search_pdb` 抛 `JSONDecodeError` | 将 204/空正文规范化为零结果，并为非法 JSON 增加受控错误 | `7F6B` 搜索稳定返回 count=0、total_count=0 | 已修复 |
| 19 | Worker 重试会重复登记同一物理产物 | 一个文件对应多条数据库记录 | 产物按 user/storage/object_key 幂等登记 | 新镜像重复登记返回同一 ID；3 条无引用历史重复记录已清理，文件保留 | 已修复 |
| 20 | 健康接口和镜像版本不一致 | 排障时无法确认实际部署版本 | FastAPI 与健康接口统一为 `0.2.2` | 公网 `/api/health` 返回 `0.2.2` | 已修复 |

## 3. 科学工具全链路证据

| 工具 | 任务 ID | 终态 | 耗时 | 结果 |
|---|---|---:|---:|---|
| 结合位点预测 `predict_binding_sites` | `2b7f9991-3921-4a1c-b38a-8a9526ab24ea` | succeeded | 26.049 s | 6 个产物 |
| PAIR 交互预测 `predict_interaction` | `8cd1c6e4-1dae-4bbe-bd54-431cd6fd974a` | succeeded | 12.743 s | 3 个产物 |
| PepCCD `generate_pepccd_candidates` | `a190b317-d672-537c-908c-89160ef58947` | succeeded | 6.231 s | 2 条候选、3 个产物 |
| Coral `generate_coral_candidates` | `3e68c94a-d084-5c34-a42d-4121875159dc` | succeeded | 6.285 s | 1 条候选、3 个产物 |
| AlphaFold3 `run_alphafold3` | `5b9f41d3-f9cb-48d3-8a4a-edac723adcb7` | succeeded | 969.231 s | 18 个产物 |

五类任务共生成 33 个已登记产物。测试账号在保留审计记录后已禁用，所有认证会话已删除；当前队列中无 queued/running 任务。

## 4. API、认证与 Agent 回归

- 注册 → `/api/auth/me` → 受保护任务列表 → logout → 未登录 me=401 → login → me：全部符合预期。
- 禁用测试账号后再次登录：403。
- 正常 Agent 对话：HTTP/SSE 200、数据库轮次 `succeeded`、无错误事件、RAG backend=`qdrant`。
- 工具失败对话：工具错误可见，但最终解释正常返回，数据库轮次仍为 `succeeded`。
- 公网 `/api/ready`：database、qdrant、task-worker、science-worker 全部 ok。
- 公网未知 API：404 JSON，不再返回前端页面。

## 5. 部署与回滚

当前镜像：

- Web：`pskit2:0.2.2-delivery`
- Worker：`pskit2-science-demo:pskit2-0.2.2-delivery`
- Qdrant：`qdrant/qdrant:v1.18.3`

上线前配置备份：`/home/enine/pskit-backups/20260822-auth-science-audit`。旧镜像仍保留，可在维护窗口把 Compose 镜像标签切回并重建 Web/Worker。密钥未写入仓库、镜像、测试记录或本报告。

## 6. 剩余运营风险（不阻塞当前交付）

1. 根分区当前约 90% 使用，仍有约 179 GB 可用。为保留回滚镜像，本次没有执行无差别 Docker prune；应安排维护窗口做定向清理和磁盘告警。
2. 当前为单机 SQLite + 单 Web 进程，限流状态保存在进程内。若扩展为多 Web 副本，应迁移 PostgreSQL，并用 Redis/网关实现分布式限流和队列公平性。
3. 公开注册按需求保留并已限流，但尚无验证码、邮箱验证或组织邀请机制；面向不受信任的大规模公网用户前建议增加。
4. Coral、PepCCD、LLM、Embedding、SerpAPI 属于外部或局域网依赖；就绪检查能发现不可用，但仍应配置持续监控与告警。
5. 本轮完成了功能、故障语义和生产回归，未替代正式渗透测试、并发压测、灾备演练和模型科学准确性评估。

## 7. 建议验收标准

- `/api/ready` 连续返回 200 且四项检查均为 ok。
- 注册、登录、退出、重新登录流程通过。
- 普通对话能够流式返回且轮次为 succeeded。
- 每类科学工具至少保留一个 succeeded 任务和可下载产物。
- 重启 Web 后 Qdrant 仍为 53 points，RAG backend 仍为 qdrant。
- Worker 或任一必需科学依赖失效时 `/api/ready` 返回 503，而不是假健康。
