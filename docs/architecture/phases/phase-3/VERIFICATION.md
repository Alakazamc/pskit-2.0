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

注入式资格 probe 的成功只证明矩阵/CAS/权限边界。尚缺实际 host driver 的逐部署、账本、Pi/Artifact 全链路测试。未调用真实模型，未访问云端数据库、凭据或容器。
