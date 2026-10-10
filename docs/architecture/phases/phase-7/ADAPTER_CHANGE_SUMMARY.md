# Phase 7 续作：持久准入与接收器观察

日期：2026-10-10。依据用户“开始干吧”继续完成本地整改；原任务限制继续有效。本轮未连接 SSH、部署云端、读取真实凭据、修改真实数据库或删除历史数据。

## 实现

- 在现有私有 PostgreSQL schema 增加独立 version 1 的发布准入组件。核心 schema 仍为 13；DDL 只由显式迁移执行。默认关闭，新配置启用时必须绑定真实持久 Pi 工作流，不能使用未受控的旧 sandbox 生命周期。
- 使用现有 `pskit-run-admission` 事务锁，把检查与 Run、Compute Job、Workspace Attempt、共享 MCP/PDF slot 和 scheduler claim 的提交放在同一事务中。暂停时仍允许已有工作完成、取消、ACK、会计结算和已完成幂等结果重放。
- 持久化完整发布身份与 nonce；同一身份可恢复，不同身份不能接管。回执过期、CLI 被杀和后端对象重建不会自动解除暂停。
- 私有 CLI 操作前后校验实际应用的 challenge 响应、数据库 deployment ID、绑定、状态、generation 和 owner。配置使用私有文件；没有 DDL、外部代理、重定向、秘密 argv 或成功常量回执。
- API 发布和回退都要求当前、候选、回退 Manifest 的准入资格一致，且 backend.env 与完整解析后的 Compose 启用相同绑定。旧 Manifest 保持可解析，旧 Web 发布兼容；缺资格的 API 镜像不能成为回退目标。
- 在现有统一 MCP/AF3 接收器进程增加可选私有 `/activity`，没有新增微服务或公开端口。检查三个固定 journal、WAL、spool 与实际执行 lane；未知、损坏、变化或缺失证据会阻止发布。
- 控制器可对每个确切 Receiver URL 从私有文件加载独立 bearer token；保留原有注入 HTTP 测试接口。
- 补齐直接 MCP/PDF 绕过暂停的问题。暂停发生在幂等领取后、真正 dispatch 前时，释放“已知未执行”的领取记录；不会错误留下 outcome unknown。已经 dispatch 的未知结果继续保守保留。
- 无幂等键的公共 MCP 调用也保存独立调用记录，未确认执行不能随容量释放而消失；原有幂等请求及成功 DTO 不变。管理台资格执行使用同一共享 MCP 容量和调用账本，保持资格报告与缓存 ACK 合同。
- 真实 PostgreSQL REPEATABLE READ 旧快照会拒绝提交；CLI 在 release 已提交却丢失应用确认时重新关闭原 owner 的门禁，保留 pending marker。完全无法启动的后端需要另行审阅的宿主恢复，不能伪造证明来自动解锁。
- 保持核心迁移原有的 13 个版本；准入组件由单独的显式安装入口执行，应用启动不做 DDL。历史 SQLite 导入/导出遇到准入组件会拒绝转换，要求 PostgreSQL 备份。迁移切换同时持有 staging/target 的迁移锁，失败清理遇到任何准入组件表都会保留 staging，避免删除或搬移未审阅的闭锁身份。

操作配置、资格与首次 adoption 边界见 [发布控制手册](../../../runbooks/RELEASE_CONTROL.md)。测试结果与尚未验收项见 [续作验证](ADAPTER_VERIFICATION.md)。

## 回退

1. 本地未启用的开发分支可按独立实现提交 revert；无需删除数据或降级核心 schema。首增量快路径可单独回退，不涉及账本。
2. 组件迁移是加法操作。保留 `release_admission_*` 表、nonce、unknown 账本、journal/outbox、工作区和历史事件；不要 drop/reset 组件来解除暂停。
3. 已启用的宿主只能回退至同样启用并通过资格的镜像，使用发布控制器原 owner 恢复流程。不能回退至不认识准入组件的旧镜像，或改 `ENABLED=false` 绕过 pending hold。
4. 第一次宿主安装、真实 Compose 重建、WireGuard listener、独立 smoke、数据库授权及 RPO/RTO 演练属于另行审阅的运维操作。本轮没有执行它们。

## 契约与目录

公开 HTTP/DTO/SSE 契约不变；新增证明路由不进入公开 OpenAPI。没有搬迁 `new_backend` / `new_frontend`，没有删除旧栈。目录迁移继续保持为独立变更和独立实机门禁。
