# Phase 4 迁移与回退

没有新数据库 schema、真实数据迁移或目录搬迁。业务工具投影的 release provenance 是服务端私有输入；公开 Receiver claim DTO 不变。旧 call id / Pending / trusted resume 语义保持。

回退以运行时、Catalog机械拆分、业务工具投影三个独立代码提交为单位。facade入口保留，可 revert 所需提交并构建旧应用制品；不要只移除模块但保留新的 import。共有 main 的 factory、环境白名单、Pi扩展与内部业务工具路由需随相应提交一致恢复。

部署本身未执行。以后回退 API 必须先冻结准入、检查活动/unknown与账本、保留当前DB和工作区，不停止或降级数据库。最多100ms/4KiB的未提交文本丢失窗口属于新合并策略；遇到不接受该窗口的需求可单独恢复逐增量持久化。
