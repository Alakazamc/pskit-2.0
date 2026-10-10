# Phase 6 回退与后续迁移

本阶段无数据库、浏览器存储或运行路径变化。回退独立文档提交可恢复旧 README；历史副本可继续保留。建议保留当前 README 的正确新栈入口。

未来纯路径提交的回退是 revert 全部 rename/path 引用，或用旧完整 checkout 构建旧制品。不要只恢复部分 import 或 source bind。不得通过回退程序降级数据库、删除工作区、清理历史产物；旧镜像/hashed assets/Manifest 保留到独立验收结束。
