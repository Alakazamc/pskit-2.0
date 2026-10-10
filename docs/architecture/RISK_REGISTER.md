# Phase 0 风险登记

基线5d70395，2026-10-10；详细证据见CURRENT_STATE_AUDIT.md。状态以审计时为准，阶段完成后追加解决证据，不覆盖原基线。

| 风险 | 级别/关联 | 本地措施与验收 | 云端或数据边界 |
| --- | --- | --- | --- |
| 应用发布停库/重新初始化 | P0 D-01 | 禁用根旧入口；app-only命令allowlist；fake docker证明无down/provision/migrate | 不执行真实release |
| readiness失败仍报成功 | P0 D-02 | 新发布入口ready+manifest校验，非零即失败；回退仅应用 | 不能自动降级schema |
| 测试captcha key/人工准入 | P0 D-03 | 全部官方测试key拒绝、缺配置拒绝；真实状态检查入口 | receiver journal需明确源；未知状态拒绝 |
| 不强制限流的模板 | P0 S-01 | local enforce429回归；生产配置预检明确执行层 | 生产有效限流仍needs_live_validation；不盲删Nginx dry-run |
| 旧回复/错误残留、同批重复 | P0 F-01 | 事件顺序重置、去重；保留plan/tool/artifact | 公开SSE类型不变 |
| SSE丢帧/超限/盲重试 | P1 F-02（基础纳入Phase1） | 有界decoder，状态分类、Retry-After、backoff/cursor回归 | 不用mock声称公网SSE可靠性已测 |
| 新栈没有CI、旧测试漂移 | P0 CI-01 | 分支门禁、PG17独立服务、固定镜像、契约再生；修真实过时期待 | CI不自动发布或用生产DSN |
| 混合alias错误宣称vision/tools | P1 M-01 | 逐部署最低能力；现有ModelPolicy扩展qualification | 兼容旧发布模型，未验收新provider不能Agent Ready |
| PinCC补丁误扩展/版本升级 | P1 M-02 | 固定1.100.3，官方Anthropic/OpenAI不变，退役探针 | 保留当前可用路由；真实资格另授权 |
| 每调用重建client/协议散落 | P1 M-03..05 | lifespan共享transport，配置builder、结构化错误 | 标题账本与授权独立；不引入新网关 |
| 调度/事件写入成本 | P1 A-01 DB-01 | 本地PG基准、有界公平候选、关键边界flush、事务回归 | 不把源码复杂度当生产P95 |
| 锁泄漏/拆模块丢状态 | P1 A-02 | refcount锁和waiter回归；现有facade内部拆分 | 维持lease/fencing/Pending/resume |
| 模型填写预算/版本 | P1 C-01 | 发布Capability业务工具投影；服务端绑定预算 | 仍用同一Job/Receipt账本，保留兼容工具 |
| OpenSandbox高权限控制面 | P1 X-01 | 文档/网络/host binds/取消ACK回归 | 没复现逃逸；执行节点分离不在此次云端授权 |
| 历史入口误删/大搬家 | P1 R-01 | 文件分类、引用清单、先模块后纯路径提交 | 目录迁移gate未通过前不搬生产引用 |
| 全量历史/CSS/草稿 | P2 F-03 | 兼容分页、主题tokens、可选清理，保留不定上传 | 不静默删除草稿；视觉验收单独记录 |
| transcript仅本地卷 | P2 A-03 | 单主机恢复/RPO说明，保留/data路径 | 本轮不实现HA checkpoint |

## 不得绕过的 Gate

1. 基线失败要定位后再引入门禁；测试skip或被中断不计passed。
2. 新供应商资格需要真实结构化工具调用/续聊证据，Mock仅能验证协议代码。
3. PG17事务/并发以独立本地DB验收，不以SQLite替代。
4. 目录迁移只在业务拆分、CI和Compose等价性通过后，独立纯迁移；当前apps结构是建议而非立即必须。
5. 生产、真实数据库、真实密钥、Staging真实供应商或执行节点操作均需新的明确授权。本次计划生成脚本与手册，不执行线上步骤。

## 2026-10-10 本地整改后状态

- D-01..03/F-01/F-02/CI-01：旧危险入口禁用、SSE正确性/边界修复、新栈隔离CI/镜像/契约门禁已实现并验证；S-01的配置与本地enforce保护通过，但生产有效限流仍needs_live_validation。
- M-01..05/C-01：Profile资格、受控driver、共享transport、结构化错误和服务端业务工具绑定已实现；旧已发布模型兼容。新增review发现的并发测试预算、退出后代理和崩溃自动重试均修复，未知预占保留。真实provider/OpenSandbox Staging仍not tested。
- A-01/02/DB-01/F-03：模块拆分、有界公平候选、delta合并、锁回收、兼容消息分页、草稿隐私和主题源已实现并验证。首持久增量P50/P95测量有回归，后续优化不能用查询量减少代替延迟证据；大历史PG EXPLAIN、真实Molstar视觉锚点仍待测。
- D/R：受控发布器本地故障与真实PG会计查询通过；缺少独立准入/Receiver适配器及Staging演练时禁止API更新，不能直接当作生产已验收工具。
- R-01/X-01/A-03：README/归档索引及控制面/恢复文档完成；目录不搬，执行节点不迁移，未实现HA。单机恢复/RPO/RTO需要实际授权演练。

所有当前结果、命令、提交边界及回滚入口见[整合报告](COMPLETION_REPORT.md)。真实数据库、用户历史、生产/Staging运行配置均未改动。
