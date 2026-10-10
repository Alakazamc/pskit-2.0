# Phase 3 — 多模型边界与资格

## 已实现

- 继续以现有 ModelPolicy/ModelDraft 为唯一权威，新增封闭的可选 Runtime Profile、revision 和脱敏资格证据。mixed alias 按所有部署的最低能力判断；明确不支持的 metadata 不能被人工声明覆盖。
- 旧持久化已发布模型保持可用；新增模型与失败候选不能通过清空 Profile 绕过资格检查，旧模型升级后也不能降级绕过。
- 抽出 PiModelConfigBuilder 与 lifespan HTTP 连接池。Run 固定服务端授权的 Profile，模型选择不接受客户端提供的上游地址或认证参数。
- proxy/标题共用 transport，各自保留授权、预占和结算。结构化错误分类后，仅明确未执行的拒绝释放预占；未知错误、5xx、断线保留 unknown，不能虚构 usage=0。
- 推理 off 与 Pi 实际 none wire 值对齐；PinCC 特例保持限定范围，普通工具业务参数不被递归过滤。
- OpenAPI 和 TypeScript 类型单独再生。原有路径、方法、字段均保留，已有 DTO 未新增 required 字段；资格端点为增量接口。

## 受控 host driver（追加提交 ff873b2）

默认关闭；只有明确配置 live/managed/Pi sandbox/Staging、独立验收账号和已核对的 Pi/Gateway 版本才允许应用初始化。普通 Run 继续走原授权路径，资格代理只接受服务端登记的内部 Run、真实 owner 和 HMAC。

逐 deployment 发现并固定当前路由指纹，每次实际请求独立预留、guard 和结算；预算在任何账本 await 前原子扣减准入容量。默认最多48次、32,768 Token、单请求120秒，正文和响应另有大小上限；取消不恢复已消耗的准入预算。异步取消等待实际数据库事务完成后再清理，已发送但用量未知保持 unknown。

真实 Pi 的本地合成探针完成 write/read、文件字节、Artifact metadata、lease-fenced artifact.created、鉴权下载和跨用户拒绝。退出工作区后撤销代理登记，并取消/等待剩余调用完成账本清理，防止过期代理继续请求。进程中断的内部验收 Run 不被普通 Agent 重试；从批量和直接领取路径排除，恢复不退还未知预占。

离线 fixture/loopback 的证据始终为 offline，不签发 Agent Ready。真实供应商付费调用、真实 OpenSandbox Staging 资格、GitHub CI 与生产可用性均未验证，也未部署；外部阶段 Gate 保持 pending。

## 提交

`cac93ce` Profile/发布资格边界；`4b787e2` transport/Pi 配置/未知预占；`530b95e` 可信 Run Profile；`bba1ee6` 单独再生可选契约。

后续提交 `ff873b2` 增加受控 driver、应用接线及内部验收恢复隔离；独立契约生成物不混入此提交。
