# Phase 4 验证

## 已完成与诚实记录

- Catalog 32/32方法+公共helper机械AST保持；独立review确认无MRO冲突。目标Catalog单测和原有Catalog全量将纳入最终combined gate。
- Session locks、PG并发窗口/admission/lease/fencing、wake/恢复、混合直接callback和batch事件序列目标22测试通过；duplicate row lock由3次降为1次并保留直接writer锁。
- 业务工具及严格旧Receiver/Pi JSON RPC：14项通过，其中PG9项零skip、已安装Pi合成Doc/Echo3项；JS9项通过。没有执行真实科研服务或GPU。
- Workspace环境白名单新回归确认允许 scoped4字段、禁止SUPABASE/DEEPSEEK/HOME秘密。
- 空delta首可见token新回归先失败（仅持久空字符串），修复后结果在最终combined gate中确认。
- 测量使用专用tmpfs PG17与随机schema，idle300秒、100/1000队列和首持久delta分位原始证据保留；详见CHANGE_SUMMARY及BASELINE。

## 尚需最终整合确认

早期combined测试1018通过/3失败/1既有external Supabase skip，PG186零skip，**不能作为全绿证明**。失败分别为新增strictReceiver测试构造漏ledger（fixture补齐）、旧wiring测试20ms任意sleep（改等待真实runner.started Event，所有授权/env/once断言保留）、PDF取消测试1秒内未观察parser进入（未修改安全断言；完整文件与20次孤立重复通过，完整运行待复核）。

最终组合结果另记完成交接。本机结果不代表GitHub CI、云端Staging或生产；未运行真实GPU/模型或改变OpenSandbox控制面。

## 最终全量整合

最终稳定业务源码 `ff873b20cdc5ecd81149f2e63542b76a32537c89`，canonical isolated runner结果 **1079 passed / 1 skipped / 221.60s；PG199 passed / 0 skipped**，Ruff/compileall通过。唯一skip为未配置的独立Supabase Auth/Storage服务，不是PG跳过。包含新的内部qualification预算/取消/lease恢复隔离，普通Run恢复/账本继续通过。

前一轮1040通过/3失败/3setup errors/1skip的六项均是 Snap Docker 看不到临时配置：当时命令漏用 CLI wrapper。使用 `PATH=/tmp/pskit-ci-docker-bin:$PATH` 后未改测试断言，最终全部通过；这是本机环境修正，不是业务缺陷掩盖。早期PDF取消未观察进入问题在当前完整运行没有复现，安全断言原样保留。

命令：`TEST_POSTGRES_DSN=<专用loopback PG17> python scripts/ci_new_stack.py backend --timeout 600 --report-dir /tmp/pskit-remediation-final-backend-green`；本机 Snap 安装另加上述PATH，CI正常Docker无需wrapper。完整log/XML在该目录，最终固定图像/契约与其它门禁见[整合报告](../../COMPLETION_REPORT.md)。
