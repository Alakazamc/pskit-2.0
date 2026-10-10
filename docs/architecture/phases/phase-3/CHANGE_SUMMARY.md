# Phase 3 — 多模型边界与资格

## 已实现

- 继续以现有 ModelPolicy/ModelDraft 为唯一权威，新增封闭的可选 Runtime Profile、revision 和脱敏资格证据。mixed alias 按所有部署的最低能力判断；明确不支持的 metadata 不能被人工声明覆盖。
- 旧持久化已发布模型保持可用；新增模型与失败候选不能通过清空 Profile 绕过资格检查，旧模型升级后也不能降级绕过。
- 抽出 PiModelConfigBuilder 与 lifespan HTTP 连接池。Run 固定服务端授权的 Profile，模型选择不接受客户端提供的上游地址或认证参数。
- proxy/标题共用 transport，各自保留授权、预占和结算。结构化错误分类后，仅明确未执行的拒绝释放预占；未知错误、5xx、断线保留 unknown，不能虚构 usage=0。
- 推理 off 与 Pi 实际 none wire 值对齐；PinCC 特例保持限定范围，普通工具业务参数不被递归过滤。
- OpenAPI 和 TypeScript 类型单独再生。原有路径、方法、字段均保留，已有 DTO 未新增 required 字段；资格端点为增量接口。

## 尚未完成

资格矩阵和受保护入口已有实现，但此阶段冻结版本的真实 host driver 尚未接到应用初始化；默认返回 `MODEL_QUALIFIER_UNAVAILABLE`。后续需要接入每次调用的独立账本、逐部署验证和 Pi/Artifact 验证，不得把注入的 fixture 成功当成真实供应商已通过。

真实供应商付费调用、Staging 沙箱资格、GitHub CI 与生产可用性均未验证，也未进行部署。此阶段完整门禁仍为 pending，核心边界代码可继续接受后续本地整改。

## 提交

`cac93ce` Profile/发布资格边界；`4b787e2` transport/Pi 配置/未知预占；`530b95e` 可信 Run Profile；`bba1ee6` 单独再生可选契约。
