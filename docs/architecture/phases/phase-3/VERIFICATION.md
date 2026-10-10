# Phase 3 验证

## 冻结源码全量回归

Git `bba1ee67f5e2e1f3b14f6c8582c6da3a6333a00b`，Python 3.12.9/Pi 0.87.1，独立 loopback PostgreSQL 17.6 临时 schema。

执行 canonical `scripts/ci_new_stack.py backend --timeout 600`。快照仅由该提交的 `git archive` 和 CI scope 生成，未包含正在开发的 Phase 4 文件、私有配置或数据。

结果：**969 passed / 1 skipped / 190.29s；PG gate 170 passed / 0 skipped**。唯一 skip 是未配置的外部 Supabase 实例；不能算作外部服务验收。存在既有 FastAPI TestClient deprecation warning。

首轮冻结快照为 968 passed / 1 failed / 1 skipped，唯一失败是新增 Profile 类型尚未再生。契约单独生成并核对旧接口兼容后再跑上述全套，没有删除断言或扩大 skip。

## 目标回归与审查

- 模型资格/旧发布/metadata：50 passed；包含发现新模型绕过资格后的 5 项红→绿回归。
- transport/Pi/错误/标题：88 passed；包含 10 项新增红→绿和实际 Pi 0.87.1 loopback wire 检查。
- 可信 Run Profile/环境传递：18 passed，包含 4 项先失败回归。
- 独立复审的 7 项原复现通过：新模型绕过、Profile 降级、未知拒绝保留预占、402 释放与 off/none 映射。
- 契约检查与旧快照比较通过：原有 paths/methods/schema properties 全保留；旧 DTO 的 required 集合未扩大。
- 固定 LiteLLM 镜像 callback matrix 结果见 Phase 2：5 tests/12 wire 子场景；这些离线用例不签发 Agent Ready。

## 限制

注入式资格 probe 的成功只证明矩阵/CAS/权限边界。这一冻结版本尚缺 host driver；下面的追加验收补齐本地实现证据。未调用真实模型，未访问云端数据库、凭据或容器。

## Driver 追加验收（ff873b2）

隔离 runner 的目标组合 **72 passed / 0 skipped / 16.00s；其中真实本地 PG17 11项零跳过**。独立 reviewer 对 Driver + 应用接线复测 **31 passed / 4.78s**，并重新验证并发预算和关闭后的代理拒绝。恢复隔离另有 SQLite28与真实PG25目标回归通过。

用例包含当前配置/权限/revision/路由 recheck；混合 deployment；attempt/Token 两种并发上限；取消发生在建 Run、预留与出站前后的事务清理；工作区退出时撤销、取消与 drain；实际安装的 Pi0.87.1调用合成文件工具并产出 lease-fenced artifact.created，metadata/内容/鉴权下载一致；跨用户拒绝；中断后未知账本保留和普通队列排除。

新增并发/取消回归首次5项失败，关闭后的请求首次仍发出一次调用，缺 artifact.created 的 PG 回归首次失败，恢复首次把未知预占127错误退到7；均观察到失败后修复。SQLite并发测试 double 的单连接后来加锁，Docker临时配置问题使用专用CLI包装入口解决；没有将真实PG替换为SQLite或删除安全断言。

证据：`/tmp/pskit-phase4-generic-qualification-reviewed-final/backend.{log,xml}`、`/tmp/pskit-qualification-independent-review.log`、`/tmp/pskit-qualification-recovery-{sqlite,pg}-final.xml`。全量结果汇总到最终交接。

**范围限制：** 实际 Pi 软件 + 本地合成模型/文件 API 不是实际 OpenSandbox/gVisor 或供应商验收。没有真实网关费用。数据库长期阻塞时取消会等待安全清理，延迟上限还需单独故障演练；未通过外部验收前默认不开 driver，也不发布新 provider。
