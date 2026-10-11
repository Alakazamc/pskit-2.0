# PR-0 / PR-1：恢复新栈 CI

## 基线与范围

基线 `c37ca61e7a8d9672013597f2cc339f4e9060720f`；PR-0 提交 `f088128`。仅提交本轮文件，不纳入工作区已有的流式顺序、准入、发布配置修改。原始任务书见 [执行方案](../../REMEDIATION_EXECUTION_2026-10-12.md)。

事实来源：[原失败 Actions 38149526876](https://github.com/Alakazamc/pskit-2.0/actions/runs/38149526876)。Backend 被 Ruff I001 阻断，未运行 pytest；Frontend 8 failed / 379 passed；下游镜像 job skipped 不能视为通过。

## PR-0

`apps/api/app/services/agent_events.py` 的 import block 后多出空行导致 Ruff I001。独立 RED 后删除一行，未修改业务逻辑。完整 Ruff、compile 及隔离 Backend：1204 passed / 1 既有外部 Supabase skipped；PG17 232 passed / 0 skipped。

## PR-1：八个用例

| 失败文件 | 原失败数 | 根因与修复 |
|---|---:|---|
| useMessageHistory.test.tsx | 1 | 查询取消发生在响应返回与 cache success 提交之间；旧确认/游标副作用提前发生，Query 通知批处理还使界面显示旧列表。把乐观消息保留在用户/会话 scoped metadata，以同步订阅合并显示；success 提交后更新确认和游标。 |
| AttachmentPreviews.test.tsx | 2 | jsdom Blob 不实现 stream，Node22 原生 Response 无法读取 fixture。用字符串 Response + image/png 生成原生 Blob，不改变预览生命周期和断言。 |
| MessageAttachments.test.tsx | 4 | 同一跨 realm Blob fixture 缺陷；保留同名 ID、待上传、重试、会话隔离和晚到结果断言。 |
| SettingsPage.test.tsx | 1 | avatar GET 的同一 fixture 缺陷；使用 image/webp Response，保留上传/删除及侧栏同步断言。 |

Node22.23.3 与锁定 npm ci 依赖复现图片 7 failed / 7 passed；修复后四文件独立重复验收。Node24 会把旧 jsdom Blob 错转成 text/plain，因此此前本地绿色不能证明 fixture 正确；新增 MIME 断言检出此问题。

历史消息额外 RED：取消边界/游标 2 fail；权威 ID 缺失、同内容不同 ID、分页缺口 3 fail；实际 send hook 漏传 ID 的 mutation 1 fail。重试已存在行与提前刷新精确 ID 边界再 RED 2 fail；最终 focused GREEN 27/27，无 skip。未删除原有 14 项。

发送接口现有 run_id 不能关联历史中的 message。按任务书允许的可选扩展加入 user_message_id：与用户消息在同事务写入 run context，幂等重试返回同 ID；旧 Run/旧服务缺少字段时仍兼容。新增身份/幂等/历史兼容测试 2/2。无数据库迁移；SSE / Compute schema 字节保持一致，仅 OpenAPI RunRef 与生成的 TypeScript 增加可选字段。

## 证据与当前门禁

原始日志、独立 RED/GREEN、JUnit 保存在 `/tmp/pskit-remediation-20261012/`，图片 `images/lane-evidence.json`、历史 `history/evidence.json`。完整本地和此提交对应 GitHub CI 的结果在后续验收记录补齐，不能以 focused GREEN 冒充完整门禁。

本地依赖：Python3.12、Node22.23.3、Pi0.87.1、一次性 loopback PG17；测试源码由 HEAD + 本轮精确文件组成，排除工作区原有未提交改动与 live .env。未连生产、SSH、真实数据库或付费模型。

## 回滚

PR-1 用 git revert 回退本阶段提交；PR-0 独立 revert f088128。新版增加可选字段，旧前端忽略，旧数据缺少时返回 null；不涉及数据库回退。前端缓存不写入 Blob URL 或新持久系统。生产仍使用原制品，不发生切流。
