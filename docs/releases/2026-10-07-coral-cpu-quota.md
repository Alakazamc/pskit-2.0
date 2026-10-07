# CORAL CPU 配额修复发布

日期：2026-10-07。发布目录：阿里云 `/home/ecs-user/pskit-agent-releases/20261007-coral-cpu-4bafe89`。

## 原因与修改

生产 CORAL 一次生成的两次提交返回 HTTP 429。只读复核确认错误为 `CPU_QUOTA_EXCEEDED`：默认 CPU 每日额度为 0，而一次生成的绑定需预留 120,000 CPU 核毫秒及 120,000 GPU 设备毫秒。三位正式用户均受此 CPU 配置影响。

后续更正：本次 GPU 检查给独立 `ComputeLedger` 指定了固定 60 分钟，未沿用应用的账号身份策略，因而“GPU 有剩余额度”和“CPU/GPU 均能准入”的结论无效。用户随后报告 GPU 拒绝；实际生产 `RESEARCH_AGENT_MEMBER_DAILY_GPU_MINUTES=0`，Compose 中的硬编码会覆盖后端环境文件。后续检查和修复见[GPU 配额发布记录](2026-10-07-coral-gpu-quota.md)。本次 CPU 变更与验证结果仍有效。

用户确认正式用户默认每天 1 CPU 核小时。生产实际配置文件 `deploy/agent/cloud.backend.pg17.env`、Staging 的 `backend.env.base` 与 `backend.env` 已设 `RESEARCH_AGENT_COMPUTE_CPU_DAILY_LIMIT_MS=3600000`。按 UTC 日计量，用户级 CPU 设置继续优先生效，GPU 限额独立计算。云端示例与首次 Staging 配置生成器同步补齐此字段。

前端使用错误码显示 CPU/GPU 配额提示，保留输入和重试能力；不展示原始上游诊断。此次也发布工具详情共用顶部标题的改动：返回箭头、工具名称和灰色简介替换 Tools 标题，工具内容直接位于其下。

## 制品与验证

| 项目 | 结果 |
| --- | --- |
| 前端及公开部署源码提交 | `4bafe89c9200040f0380a6ffba6407e0ea446eb4` |
| 相关前端测试 | 49 passed；typecheck、lint、build 通过 |
| 配置测试 | 7 passed |
| 浏览器检查 | 桌面 1440 px、手机 390 px，两种主题的标题、返回、编辑、简介及无横向溢出均通过；使用 API 替身 |
| Staging 验收 | 登录、上传、SSE、配额、模拟 AF3 通过，4 个事件；生产只读快照未变 |
| 生产复核 | CPU 默认额度 3,600,000 核毫秒，三位正式用户能满足一次生成的 CPU 预留；GPU 检查方法有误，已在上文更正 |
| 后端镜像 | 保持 `pskit-agent-backend:20261007-downloads-af3-tools`，只定向重建后端以加载配置 |
| 后端镜像 ID | `sha256:d3a04483ccb92ae8b477e57e5e850061bcf598d355e3c6e2533e5c9614fd343e` |
| 前端树 SHA256 | `c373ebcbb7b27d053ca62474a276c6d413d3e3695270f8c070536794f337bd6c` |
| index SHA256 | `056f118a77832cd2b5729ec5a3cafddd367c9cea522a65032da17e2dbeb88305` |
| 传输归档 SHA256 | `9e118a8d315ada79c7467642e2de264084785a4d6d40adc96d54762ebe91d58d` |
| 云端检查 | 登录页及四个入口资源内容一致；未登录 usage 401、internal 404、公网 admin 403 |

本次验证配额准入和界面行为，没有启动新的真实 GPU 任务。生产重建前后均为 24 个已完成 Agent Run、1 个历史失败 Run、7 个已完成计算 Job；未迁移或重置数据库。

## 证据与恢复

发布目录保留 `release.json`、`staging-cpu-quota.json`、`production-cpu-quota.json`、`staging-static.json`、`production-static.json` 和同机 `rollback/`。后者包含原私有环境文件、完整静态目录及公开源码副本。配置备份保持私有权限；恢复原 CPU 配置后需要定向重建后端，恢复静态目录只需原子替换 index。恢复配额为 0 会重新阻止需 CPU 预留的任务。
