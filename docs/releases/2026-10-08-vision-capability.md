# LiteLLM 视觉能力合并修复发布

日期：2026-10-08。源码提交：`23f0efc4c208a3dcaa19371a6031d853d5eab408`。

## 内容

LiteLLM 的 `/v1/models` 和 `/model/info` 可能同时返回同一模型的能力信息，其中一个来源明确报告 `supports_vision=true`，另一个来源没有确认。旧逻辑要求所有来源都为真，导致已经支持多模态的模型仍拒绝图片。`ModelCatalog` 现在使用 `any(info.get(model_id, []))`：只要任一可信 LiteLLM 元数据来源明确确认视觉能力，就允许该模型接收图片；显式配置的图片模型白名单仍保持优先。

本次只更新阿里云 Staging 和生产 backend。前端、PostgreSQL、Supabase、LiteLLM、Nginx、A6000 通用接收器、AF3 与 CORAL provider 均未重建或重启。

## 制品

| 项目 | 值 |
| --- | --- |
| 后端镜像 | `pskit-agent-backend:20261008-vision-23f0efc` |
| 阿里云镜像 ID | `sha256:3f7dab0c322545be70ca860499bab8ce4edf223626e15a67b12eb88125560726` |
| revision label | `23f0efc4c208a3dcaa19371a6031d853d5eab408` |
| 发布目录 | `/home/ecs-user/pskit-agent-releases/20261008-vision-23f0efc` |
| 镜像归档 SHA256 | `3827cf5811b05f826450a11e597dcd086a21ab978dc8c85d0aad29d0a013e0bd` |
| 源码归档 SHA256 | `b52443648a7968ecab673d07e9052ff30b6398f5e50a6ab15749c171921726e8` |
| 依赖清单 SHA256 | `7dc381fe3891a3b1fc556a83a97b632c7390bf13426b712cb9257b36ad6b3aff` |

本地构建使用 Dockerfile 固定的 Python/Node digest 对应的已验收本地镜像引用，复用清华 PyPI 与 npmmirror 缓存层；构建没有访问不稳定的 Docker Hub metadata。新旧生产镜像的 `pip freeze` 完全一致，镜像内导入和修复代码探针通过。

## 发布与验证

- 同一镜像先发布到隔离 Staging。运行容器使用上述镜像 ID并进入 healthy；直接 readiness 200、私网登录页 200、未登录 usage 401。
- 生产切换前先确认 Agent Run、计算 Job 和待对账用量均为空。停止旧 backend 后，再通过一次性旧镜像容器查询同一生产数据库，仍为空，随后只修改 `AGENT_BACKEND_IMAGE` 并定向重建 backend。
- 生产 backend 为 healthy，readiness 200；公网登录页 200、未登录 usage 401、internal 404、公网管理入口 403。
- 生产 `ModelCatalog` 实际读取 LiteLLM 元数据后返回 21 个可见模型，当前全部标记 `supports_images=true`；该检查只读取元数据，没有调用模型或产生 Token 费用。
- 切换后再次确认没有非终态 Run、Job 或待对账用量。

本次没有发送真实图片给模型。图片消息的完整供应商调用仍由用户正常对话触发，会按所选模型产生 Token 费用。

## 回滚

发布目录 `rollback/cloud.env` 保存切换前的私有生产 pin，`rollback/previous-backend.json` 保存旧容器身份。旧镜像为 `pskit-agent-backend:20261007-streaming-a28b9af`，ID `sha256:d713d9b57327ac231462c8b229cfaebb35178ba88413145409b8cebc3cc73e5f`。回滚前再次确认没有活动 Run/Job，将 `AGENT_BACKEND_IMAGE` 原子恢复后，使用相同 Compose 文件和项目名只重建 backend。Staging 的上一组 pin 位于 `/home/ecs-user/pskit-agent-staging-private/release-backups/pins-sus52je2`。
