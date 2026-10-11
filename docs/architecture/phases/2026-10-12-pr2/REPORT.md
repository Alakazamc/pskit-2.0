# PR-2：公开工具摘要的安全边界

基线 `eb8df7027e85926145ac541a1c446b45104c2a08` 已完成 [六项远端门禁](https://github.com/Alakazamc/pskit-2.0/actions/runs/38156874714)。PR-0/1 本地最终：Backend1206通过/1原有外部Supabase跳过（PG232零跳过）；Frontend400通过，四个失败文件41项连续三轮通过；Deployment449、镜像3、固定LiteLLM5、Pi入口、契约再生及9组合成Compose通过。

## 事实与最小修复

修改 `apps/api/app/services/agent_events.py`，新增 `apps/api/tests/test_tool_summary_security.py`；原有 `test_tool_events.py` 仅把原始输出截断的旧预期改为固定的 Tool running，保留生命周期、SSE、持久消息和私有结果负面断言。工作区先前的流式顺序修改不属于本提交。

旧进度直接取 raw partialResult 最后一行；旧失败 regex 无法阻止 JSON 引号中的密钥、Basic 凭据和科研输入进入公开摘要。统一 `_tool_summary` 按生命周期、精确错误码白名单、非零整数退出码选择固定文本；无任意文本、路径、输入或错误插值。未知错误保持 failed + Tool failed；合法状态/事件类型保持；原始结果不被修改。这里只约束公开 Tool 摘要，原始结果仍沿既有授权路径供 Pi 使用，不声称过滤模型回复中的任意秘密。

异常 details.status 类型通过字符串检查避免 list/dict hash 崩溃。不是把失败转成功，也不调整现有合法状态映射。

## RED / GREEN

- 原始安全矩阵 RED：80 failed /16 passed。
- 补充分类 RED：7 failed /96 passed。
- 异常状态 RED：2 failed /113 passed。
- 最终安全回归 GREEN115/115，无skip。相关文本缓冲、模型结果账本、Run lease focused回归也通过；全量结果及此阶段远端 SHA 在最终验收补齐。
- 负面输入包含 Bearer/sk/JWT/Authorization Basic、JSON字段、查询Token、URL凭据、控制字符/ANSI、长二进制/日志、私有科研输入；同时验证 events/message 投影，不只是 regex 替换示例。

完整 stdout/JUnit/执行记录：`/tmp/pskit-remediation-20261012/pr2/`。无付费或生产调用、数据库迁移、API/SSE schema改变。

完整安全候选最初为1320通过/1失败/1原有外部Supabase跳过，失败是未被本补丁修改的 MCP 跨实例容量用例。保留该失败，另行复现默认线程池饱和导致续租排队的既有正确性缺陷，并采用[独立 MCP 续租修复](../2026-10-12-mcp-capacity/REPORT.md)。安全补丁与该独立修复整合的完整 CI 为 **1326 passed /1既有外部Supabase skipped，PG232 passed /0 skipped，318.57s**，Ruff/compileall通过。不能据此声称单次容量失败的具体调度原因已经证实；确定性饱和用例与原跨实例用例均保留。

远端验收以整合后的精确 HEAD 为准，Actions SHA/链接在最终交付索引补齐；不引用旧 HEAD 代替本阶段结果。

## 回滚与限制

用 git revert 回退本阶段提交，原事件类型和账本不需要回退；生产当前未部署本提交。注意回退会恢复原始摘要暴露路径，应仅用于本地比较或替换修复，不能据此宣称安全。已有历史事件不自动重写或删除；管理员诊断通道与模型结果的权限仍按原策略执行。
