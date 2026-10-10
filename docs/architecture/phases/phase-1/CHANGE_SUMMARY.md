# Phase 1：正确性与部署安全

日期：2026-10-10。基线 `5d70395`；分支 `codex/new-stack-baseline`。本阶段仅本地
修改，不发布、不修改真实配置或数据库。对外 API/RunEvent 字段保持不变。

## 流式输出（F-01/F-02）

- `features/chat/events.ts` 按顺序清空跨批次累积正文/旧错误；同批与回放去重。
  既有计划、完成工具和产物继续保留，cursor 不因重复事件后退。
- `api/sse.ts` 为快照和 live 提供同一增量帧解析器，覆盖 CR/LF/CRLF、UTF-8、
  多行 data、注释、坏 JSON、EOF 残帧；单帧原始字节上限 1,000,000。
  同一个网络块里有效前缀先交付，超限尾帧仍拒绝。
- HTTP 保留 status/code/Retry-After；重连区分永久错误、429、网络/5xx 和主动
  取消，采用有界指数退避、抖动和 cursor 续接。终态通知至多一次。

## 发布入口（D-01/D-02/D-03/S-01）

- 根 `deploy_to_cloud.sh` 退役：包括 help/rollback 在内的所有参数只打印说明，
  返回 2。没有 SSH、停库、provision、迁移或测试 key 回退分支。
- 新纯 Python 生产认证预检要求 enforce、captcha、明确可信代理、独立足长
  secrets、生产 hostname，并拒绝全部官方公开测试 keys。字面量解析支持
  Compose 注释；认证字段拒绝变量展开、转义和内侧首尾空白。
- `stack.sh check-config` 在 Docker 前检查配置；全栈 up/down 的初始化/维护
  职责不等同日常应用发布。生产示例要求 enforce，真实配置未更改。
- [应用发布手册](../../../runbooks/DEPLOYMENT.md) 使用完整 optional
  OpenSandbox overlays、backend-only `--no-deps --wait` 和 `/health/ready`；
  smoke 失败必须非零。数据库迁移单独审批执行。

生产 Nginx dry-run、真实 enforce/Turnstile/可信 IP 与受控 429 仍待授权验收，
没有将模板或本地 HTTP 替身测试写成线上防护已生效。下一阶段解决新栈 CI、
测试基线和已漂移生成契约，生成物同步与业务修改分开提交。
