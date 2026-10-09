# OpenSandbox、gVisor 与 Pi 工作区工具发布记录

日期：2026-10-09（Asia/Shanghai）
分支：`codex/new-stack-baseline`

## 发布范围

本次完成 OpenSandbox 1.1.0 provider、每用户持久工作区、`/workspace/<session_id>`
会话目录、受控文件/命令工具、Artifact 回收、gVisor `runsc`、配额 ext4 卷、
Staging 资格验收和生产发布。生产先以单用户 files-only 验证，随后于
2026-10-09 按用户决定依次全量开放文件与 Bash/Python 工具。A6000 通用 MCP/AF3 接收器、AF3 计算容器、4090
CORAL、Supabase、LiteLLM 和生产前端均未重建。

## 固定制品

| 制品 | 固定值 |
| --- | --- |
| 后端源码 revision | `3912773d10f0d4214562de33da4976ffe0de13e9` |
| 后端镜像 ID | `sha256:6c8cfe2edf465b003eec11baa7a49013a75ea1255e9a54ae01c65f5e877bcbb3` |
| 工作区源码 revision | `9fb8b702c5318fe54de15d568321de79d80c3562` |
| 工作区镜像 ID | `sha256:1a8fd01fde3c64ecad3036941c344befc214a3b4e983b7fd8c970554d5c97dcc` |
| 工作区 registry digest | `127.0.0.1:5000/pskit/workspace@sha256:7a038231ce80e0ddbaca47756e69d7c06c76484f6f525d625e66280553830fcb` |
| 工作区 Python base | `docker.m.daocloud.io/library/python@sha256:2b4f19dae3a777dfc3b76730bda1e82e1f66ab2a2686fa93ca78edbfb4f04ffe` |
| OpenSandbox Server | `127.0.0.1:5000/pskit/opensandbox-server@sha256:1fa6564ab705348f610c90ee6f7878c78cd2ac46f59a8a84fa9194683d382c86` |
| OpenSandbox Server commit | `b1a29cf93a823a95913f7943010febb3f29de05c` |
| execd | `127.0.0.1:5000/pskit/opensandbox-execd@sha256:30f83b875300123a2223ffedb4dd79a245c291ebb060193178d730b92dec0b00` |
| egress 版本矩阵 | `sandbox-registry.cn-zhangjiakou.cr.aliyuncs.com/opensandbox/egress@sha256:56429c89b7175c2a24af62ca93a99776f03ea67a0beb74d63c8ddb34ff16fd97` |
| 前端 dist 哈希 | `718acecb643aff6d6435c1e3fdd242ad588c0fd9c5b30431b0135c71ecfc57b9` |
| 后端源码归档 SHA256 | `1b76abb23fea5cb3300baf040a828babd686abfd3e893fcd0c4a0512d940001a` |

部署脚本修复持续到 `0f3f01b`：资格验收使用独立 namespace；配额卷探针模拟
可信 bootstrap；live probe 显式使用镜像 `/app`；rollout 以 `0755/0644` 提供给
UID 10001 的只读 bind mount。文件不含 API key、JWT、数据库 DSN 或模型凭据。

## 宿主与 Staging 证据

- gVisor：`release-20261005.0`，注册 runtime 为 `runsc`；安装包 SHA512 为
  `79869ae9a589355a46d46fdac068d9a954e741741ffe77c64e38c56ea955498c1cb04e9bce44e1cce5155333e6b76bee72347c6661bb7c78fd2fe3aad198c8fe`。
- 配额卷：`pskit-quota` 插件 1.0.1，根目录 `/data/pskit-quota-volumes`；安装及
  再安装均未重启 Docker，既有容器保持 `runc`。
- Staging manifest SHA256：
  `153f0a9d2575866bc5fb0f75d6c907d91c090b37b58cbd1800a9817dd66d1603`，
  状态 `qualified`。
- capability hash：
  `24fddff87b8a7775e49b20f423bc3641a62cdb47697063ba4384dec8c40cd977`；
  evidence SHA256：
  `9bce0e90c7961275f7ce8afd21bb3feffd71e884bbeaa44d096033f56bb54dd6`。
- 实际 `runsc` smoke 通过：两个用户隔离、同用户两个 Session 隔离、只读文件目录、
  command events、取消确认、输出截断、Artifact 持久化、stop/replace 后卷保留。
- Staging 私有 API 与 WireGuard Nginx 两层 smoke 均通过登录、文件、SSE、额度和
  模拟 AF3，各收到 4 个 SSE event；未调用真实模型或 GPU。
- 失败关闭曾发现并修复三处发布门问题：工作区缺少稳定 `/usr/bin/python3`、
  外部资格探针与 orphan reconciler 竞争、rollout bind 权限不可读。每次失败时生产
  rollout 均为关闭状态，旧后端继续运行。

## 生产状态

- backend 容器健康，使用上述固定镜像 ID 与 revision；OpenSandbox Server 使用固定
  Server digest，未发布宿主端口。
- provider readiness：`WORKSPACE_READY`、runtime `runsc`、文件、命令执行能力、
  持久卷及 Session mount namespace 均已由 capability probe 证明。
- rollout：`basic-tools`，`user_allowlist=["*"]` 对所有用户开放，
  `commands_enabled=true`；
  活动/unknown workspace attempt 为 `0|0`。
- 路由验收：登录页 200、未登录 `/api/v1/usage` 401、`/internal` 404、
  公网管理入口 403；生产与 Staging readiness 均为 200。
- 没有遗留运行中的 `runsc` 探针容器。失败资格测试产生的四个 Staging 测试沙箱
  已停止，卷按设计保留。
- 生产私有配置回滚副本：
  `cloud.env.pre-opensandbox-20261009T084231Z` 与
  `.env.stack.pre-opensandbox-20261009T084231Z`。这些文件只留在阿里云宿主机。

## 同日全量文件工具更新（12:58 UTC）

- 根因核对发现，新近对话用户不在原单用户 allowlist 中，因此后端在
  创建 workspace attempt 前直接跳过，Pi 未收到文件工具。
- 变更前活动/unknown attempt 为 `0|0`；第一阶段原子将策略改为
  `enabled=true`、`user_allowlist=["*"]`、`commands_enabled=false`，仅开放文件能力。
- 未重建或重启 backend、OpenSandbox Server、Supabase、LiteLLM、A6000 接收器或
  前端。Readiness 继续返回 `WORKSPACE_READY`。
- 发布脚本同步改为默认写入 `user_allowlist=["*"]`，不再接受单用户
  `--allow-user`。
- 线上策略回退副本：
  `policy.json.pre-full-20261009T125811Z`。

## 同日基础命令工具更新

- 用户确认不增加产品专用 Artifact 工具，使用 Pi 基本工具
  `read/write/edit/ls/find/grep/bash/python`。
- 沙箱基础镜像已使用固定 Python 3.12 slim digest；无须更换镜像。
- 生产在 Staging 验收 capability hash 下启用命令工具。Bash/Python 只在 gVisor
  Session `/workspace` 内执行，受每日 1 CPU 核小时、1000 millicores、10 GiB
  磁盘、超时与输出限制，不可访问宿主文件系统或 Docker socket。

## 回退

先关闭新 workspace 调用、等待活动 attempt 排空，再运行：

```bash
cd /home/ecs-user/pskit-agent-cloud-20261002
bash deploy/agent/scripts/rollback_opensandbox_release.sh --drain-seconds 120
```

回退脚本拒绝 unknown attempt，不执行 `down -v`，不会删除用户工作卷、Artifact、
对话或数据库记录，也不会恢复 raw Pi built-ins。若只需立即撤销用户访问，可先用
`set_workspace_rollout.py` 写 `--no-enabled --no-commands-enabled`，无需停止 Server。

## 后续

观察全量用户的 provider error、冷启动、工作区容量、命令配额和 Artifact 行为。
