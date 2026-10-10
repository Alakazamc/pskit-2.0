# Phase 4：运行时、目录内拆分与业务工具边界

## 实现

- 原 AgentService 导入/构造/公开方法保留 facade；scheduler、context、executor、resume、workspace、typed event projector 在原目录拆分，不新建服务或第二套调度器。
- Session lock 按 holder/waiter 引用计数回收；取消与仍有等待者时不早删锁。PG 事务整体搬到线程异步边界，取消等待数据库实际提交/退出；不在逐 SQL 间切线程。
- 调度器保留 advisory admission lock、全局/用户/session 额度、lease/fencing。PG 原生候选查询先过滤已满用户，再有界窗口和 cursor；单个满用户前缀不会阻塞其他用户。wake Event 与空闲退避保留跨进程回退轮询。
- 第一个可见 token 立即持久化；后续相邻文本最多100ms/4KiB合并。tool、artifact、retry、terminal 前 flush。仅未提交文本窗口可能在进程崩溃时丢失，critical events/账本不缓冲。空 provider delta 不消耗首可见 token 的立即标记。
- CatalogStore 保留构造器和 facade，Skill/File/Tool 32个方法机械拆分；独立 AST 审查确认32/32方法及共享 helper/常量一致。
- 已发布、可见、单能力 Tool Product 可以投影为业务工具。模型只看到业务 input schema；release/action/version/budget/endpoint 由服务端绑定，每次调用重验权限，call id 保持幂等。多步 workflow 仍走现有 Tool Run，不压平成单工具。旧 submit_compute 保持可用。
- Provenance 只影响私有 request hash，未向公开 claim binding 添加新字段；旧 extra=forbid Receiver 契约可以接收。动态工具结果保留真实工具名和 tool_call_id。
- Sandbox Pi 环境显式允许会话、attempt、workspace token/能力及业务工具描述，禁止继承宿主秘密环境。

## 测量结论（不是首token全项优化）

基线 bf3abbc 与 Phase 4 隔离测量快照（逐文件哈希见原始 JSON），PG17、本机合成Pi5ms；完整原始数据见 BASELINE.md 和 after-*.json。不是实际付费模型/GPU/browser容量测试。

| 指标 | 基线 | 当前测量 |
| --- | --- | --- |
| 300秒空闲显式SQL | 30,927 | 5,972（首次after快照，事件锁小修不影响空闲路径） |
| 满用户前缀100/1000候选 | 402/4,002 SQL | 4/4 SQL |
| 100 Run排队完成 | 5.024s / 10,920 SQL | 2.980s / 5,199 SQL |
| 1000 Run | 180s初测未完成；独立50.777s仅132完成 | 29.572s / 51,862 SQL，1000全部完成 |
| 结束保留Session锁 | 100 Run后100个 | 100/1000后0 |
| 256个连续delta INSERT | 256 | 4（first+有界后续） |
| 即时admission→首持久增量50样本P50/P95 | 24.896/28.153ms | 34.460/46.586ms |
| provider emit→首提交P50/P95 | 无同观察器基线 | 4.625/6.259ms |

首增量P50/P95发生回归，异步事务边界和调度开销仍需后续优化；不把查询减少描述为首token延迟改善。第一轮after观察器曾轮询SQL，造成额外阻塞，已保留原结果并换内存observer；当前结果仍回归。随后资格 driver 的 crash fence 为候选查询增加了内部 purpose 排除条件；本表不是该追加修复后的重新测量。1000 Run基线不是完整排空，不能算相同完成数的倍率。每轮源码文件哈希与环境在原始JSON中；没有伪称已在云端测量。

## 主要代码边界

`app/services/agent.py` 保留 AgentService facade，内部职责移至 `agent_scheduler.py`、`agent_context.py`、`agent_executor.py`、`agent_resume.py`、`agent_workspace.py`、`agent_events.py` 和 `session_locks.py`；候选与领取在 `domain/persistent_conversation/{admission,runs,recovery}.py`。Catalog 使用 `domain/catalog_{common,skills,files,tools}.py`。业务投影在 `services/compute_tools.py` / `api/internal_compute.py` 与 `pi/extension.js`，旧公开claim binding无新增mandatory字段。
