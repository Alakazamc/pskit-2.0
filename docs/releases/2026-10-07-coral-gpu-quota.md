# CORAL GPU 配额配置修复

日期：2026-10-07。公开配置提交：`005244e2eae692139c787e476fb6c6c283e11c60`。阿里云发布目录：`/home/ecs-user/pskit-agent-releases/20261007-coral-gpu-005244e`。

## 原因

CPU 修复后，CORAL 一次生成仍返回 HTTP 429、`GPU_QUOTA_EXCEEDED`。只读检查沿用应用实际构造的 `IdentityPolicyStore`、`PersistentConversationStore._gpu_limit_for` 和 `ComputeLedger`：正式用户的有效 GPU 上限为 0，并没有未释放的任务预留。一次生成需预留 120,000 GPU 设备毫秒，即 2 分钟。

根因是 `compose.cloud.yaml`、`compose.postgres.yaml`、`compose.staging.yaml` 的 `environment` 把会员 GPU 配额写死为 0，其优先级高于 `env_file`。此设置源于初期私网验证。第一次仅修改 Staging 环境文件时，合并配置预检检测到该覆盖，自动恢复；生产未执行此次失败的更新。修复后重新进行 Staging 验证，再发布生产。

此前 CPU 发布记录中的 GPU 检查使用固定 60 分钟，遗漏身份策略与 Compose 覆盖，已在原记录中更正。

## 修改

- 用户确认正式用户默认每天 60 GPU 设备分钟；游客仍为 0；个人显式额度继续优先。
- 删除三个当前云端 Compose 文件的会员 GPU 硬编码，由实际后端环境文件统一配置。
- 生产 `deploy/agent/cloud.backend.pg17.env`、Staging `backend.env` 和 `backend.env.base` 明确设置会员 60、游客 0。CPU 每日默认仍为 3,600,000 核毫秒。
- 补齐公开环境示例、首次 Staging 配置生成器和运行文档。
- 只定向重建两套环境的 backend；镜像、Pi 模式、数据库、前端、其他服务和历史用量保持原值。

## 验证

| 检查 | 结果 |
| --- | --- |
| 实际 Compose 覆盖复现 | 环境文件为 60 时合并为 0，真实 FastAPI CORAL POST 返回 429；修复后 POST 返回 200、queued |
| 配置边界 | 云端、PostgreSQL、Staging 三种叠加均尊重 60、7、0 分钟；游客 0 |
| 实际 API 用量 | 新任务预留 120,000 GPU 毫秒，`/usage` 显示 2 分钟预留；个人 0 拒绝且无残留 Run 或预留 |
| 回归 | 部署完整测试集与相关 PostgreSQL 配额、计算、工具 API／事件测试共 177 passed；Ruff 通过 |
| Staging | 登录、文件、SSE、配额、模拟 AF3 通过；4 个事件；生产只读快照未变 |
| 生产实际配额 | 三位正式用户有效 GPU 默认均为 60 分钟，均能满足 CORAL 四种操作当前的 CPU/GPU 预留条件 |
| 当前管理员账号 | GPU 已用 0、预留 0、剩余 60 分钟 |
| 历史状态 | 重建前后 25 completed Agent Run、1 个历史 failed Run、7 completed Job；原用量和预留一致 |
| HTTPS | 登录页 200；未登录 usage 401；internal 404；公网 admin 403 |
| 后端镜像 ID | `sha256:d3a04483ccb92ae8b477e57e5e850061bcf598d355e3c6e2533e5c9614fd343e`，未改变 |
| 公开配置归档 SHA256 | `5437fbc96232580f8f0f3e85bc2f151371097b4eb2ac9fd8679d20224b7894d3` |

测试使用隔离数据库，未启动 Pi 或远程 GPU 计算。生产复核为只读实际准入与额度检查，没有发起新的真实 GPU 任务，因此不代表新的 CORAL 推理结果已验证。测试有一条现有 Starlette/httpx 弃用提醒，没有失败。

## 证据与恢复

发布目录保留 `release.json`、`production-quota-before.json`、`production-quota-after.json`、两套 `*-gpu-quota.json` 与 `staging-smoke.log`。`rollback/public-source` 保留原公开文件，`rollback/staging` 和 `rollback/production` 保留同机私有环境文件，权限为 0600。恢复时使用对应路径记录还原配置并定向重建 backend；不得覆盖数据库或清除历史用量。恢复旧会员 0 配置会重新禁止 GPU 任务。
