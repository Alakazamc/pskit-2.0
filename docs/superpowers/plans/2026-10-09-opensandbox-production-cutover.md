# OpenSandbox Staging 与生产灰度实施计划（阶段 C）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Staging 证明固定制品和实际 gVisor 隔离后，把 OpenSandbox 工作区工具安全灰度到阿里云生产，并保留即时关闭工具、保留卷和恢复旧行为的回退路径。

**Architecture:** 使用同一后端镜像、sandbox 镜像和前端制品先部署独立 Staging。宿主安装固定版 `runsc` 是单独的 root 操作；发布脚本在切换前读取实际 Docker runtime、OpenSandbox capability report、数据库 schema 和镜像 digest。生产先对管理员 allowlist 开放，再逐步扩大；旧 manager 不在生产同时运行，源码删除另做变更。

**Tech Stack:** Docker Compose、gVisor `runsc`、OpenSandbox Server 1.1.0、Nginx、PostgreSQL、现有 `stack.sh`/Staging/发布 skill。

**Spec:** `docs/superpowers/specs/2026-10-09-opensandbox-gvisor-pi-tools-design.md`

**Depends on:** 阶段 A 完成；阶段 B 的命令部分只有在 A 的 command capability gate 通过时才能进入灰度。

## Global Constraints

- 部署顺序固定为本地实现 → 独立 Staging → 管理员生产灰度 → 正式用户逐步开放。
- Staging 与生产使用不同 namespace、API key、数据库 schema/数据、网络、卷和探针用户；不迁移测试数据到生产。
- `runsc` 必须固定版本并由 root 注册到 Docker；脚本不得无提示修改 daemon 配置或重启 Docker。
- root 安装、Docker daemon reload/restart 和宿主 Nginx 修改由具备 root 权限的一方执行已审阅脚本。
- 生产切换使用功能开关和用户 allowlist；关闭开关不会删除用户卷、artifact 或 transcript。
- 不在首次切换中删除旧 manager 代码、旧卷或历史数据；确认稳定后另立退役任务。
- 按当前会话要求，本计划不新增或运行自动化测试；实际验证和部署只在用户明确要求验证/发布后执行。
- 云端发布遵循 `skills/pskit-cloud-deploy/SKILL.md`，发布后更新独立发布记录和 `docs/DEV_CLOUD_CONTEXT.md` 交接。

## Review Focus

1. **宿主变更风险：** 安装/注册 `runsc` 不应影响现有 AF3、CORAL、Supabase、LiteLLM 和普通 backend 容器。
2. **同制品原则：** Staging 通过的镜像 digest、sandbox digest 和前端 dist hash 必须与生产完全一致。
3. **双 provider 冲突：** 同一环境不能同时启用旧 `compose.sandbox.yaml` 和新 `compose.opensandbox.yaml`。
4. **回退语义：** 回退关闭 workspace 工具，不把不安全 local shell 当 fallback，也不删除用户卷。
5. **灰度授权：** allowlist 由后端身份决定，浏览器不能自行开启；撤销后下一次工具调用立即拒绝。

---

### Task C1: 宿主 gVisor 安装和只读预检脚本

**Files:**
- Create: `deploy/agent/scripts/install_gvisor_runtime.sh`
- Create: `deploy/agent/scripts/check_gvisor_runtime.sh`
- Modify: `deploy/agent/OPENSANDBOX.md`

**Interfaces:**
- 安装脚本接收固定 release/version、已下载包路径和 checksum；不使用 latest URL。
- 检查脚本只读输出 Docker runtime 注册、`runsc --version`、探针容器 runtime 与现有容器状态摘要。

- [ ] 安装脚本先校验 checksum、发行版和架构，生成待写配置并显示差异；没有 root 或显式 apply 参数时只预览。
- [ ] apply 前备份 Docker daemon 配置；合并 `runsc` runtime 而不是覆盖其他 runtimes。
- [ ] Docker reload/restart 前记录现有关键容器并等待用户执行；完成后确认它们仍使用原 runtime 且健康。
- [ ] 检查脚本用固定无网络探针镜像运行 `--runtime=runsc`，不读取或输出云端秘密。
- [ ] 手册给出失败时恢复 daemon 配置、停止探针和保持现有生产容器的方法。
- [ ] Commit: `feat(deploy): add controlled gvisor host setup`。

### Task C2: Staging OpenSandbox overlay 和发布清单

**Files:**
- Create: `deploy/agent/compose.opensandbox.staging.yaml`
- Create: `deploy/agent/scripts/prepare_opensandbox_release.py`
- Create: `deploy/agent/scripts/opensandbox_staging_smoke.py`
- Modify: `deploy/agent/staging.sh`
- Modify: `deploy/agent/STAGING.md`

**Interfaces:**
- 发布 manifest 锁定 backend image ID/digest、sandbox digest、OpenSandbox images、SDK/server 版本、runsc 版本、Compose render hash 和 frontend dist hash。
- Staging 使用独立 provider namespace、schema、API key、network、volume 前缀和测试用户。

- [ ] manifest 生成器拒绝 tag-only 镜像、缺 digest、缺 runsc、公开 server port、与生产相同 namespace/key/volume/network。
- [ ] staging overlay 启用 OpenSandbox provider，但默认 command tools 关闭；文件能力通过后再单独打开 command flag。
- [ ] smoke 脚本覆盖两个用户、同用户两个 Session、路径 `/workspace/<session_id>`、上传/读取/写入/产物、stop/reattach、replace 保卷。
- [ ] command gate 覆盖实际 runsc、跨 Session 隐藏、宿主/后端/其他用户不可见、数据库网络拒绝、`/proc/1/environ`、ptrace、提权、reserved env 和 cancel exit proof。
- [ ] 连续运行期间检查冷启动、并发 ensure、空闲回收、provider 重启、卷满/inode 满、日志截断和无 fallback。
- [ ] Commit: `feat(deploy): stage opensandbox release manifest`。

### Task C3: 生产配置、用户 allowlist 和即时关闭开关

**Files:**
- Modify: `new_backend/app/config.py`
- Modify: `new_backend/app/main.py`
- Create: `new_backend/app/domain/workspace_access.py`
- Modify: `new_backend/app/services/agent.py`
- Create: `deploy/agent/compose.opensandbox.production.yaml`
- Modify: `deploy/agent/cloud.env.example`

**Interfaces:**
- `RESEARCH_AGENT_WORKSPACE_ENABLED=false|true`
- `RESEARCH_AGENT_WORKSPACE_USER_ALLOWLIST_JSON=[]`
- `RESEARCH_AGENT_WORKSPACE_COMMANDS_ENABLED=false|true`
- 服务端 access policy 在每次工具调用读取当前发布配置；客户端状态不是授权依据。

- [ ] 默认关闭全部 workspace 工具；生产首次启用只允许明确管理员 user ID。
- [ ] 文件工具和 command 工具使用独立开关；command 开关还必须满足 capability report hash 与当前 release manifest 一致。
- [ ] 撤销用户后不删除卷；新调用立即拒绝，正在执行的命令按运维选择 drain 或 cancel 并记录结果。
- [ ] Compose 禁止同时叠加旧 `compose.sandbox.yaml`，且 OpenSandbox API 不发布公网端口。
- [ ] readiness 失败时 Nginx/backend 不把请求路由到一个会本地执行的旧分支。
- [ ] Commit: `feat(sandbox): add production workspace rollout policy`。

### Task C4: 发布、观测和回退脚本

**Files:**
- Create: `deploy/agent/scripts/deploy_opensandbox_release.sh`
- Create: `deploy/agent/scripts/rollback_opensandbox_release.sh`
- Modify: `deploy/agent/stack.sh`
- Modify: `deploy/agent/DEPLOYMENT.md`

**Interfaces:**
- deploy 只接受 Task C2 的 manifest；先 preflight、迁移、启动 provider、检查 readiness，再切 backend 配置。
- rollback 关闭 workspace access、drain/cancel 活动 attempt、停 OpenSandbox 实例，保留 volume 和数据库映射；普通 Pi 聊天继续 local control-plane 模式。

- [ ] 切换前记录容器、schema、镜像、runtime、健康、active attempts 和关键配置 hash；输出不含 secrets。
- [ ] migration 先于新 backend，旧 backend 版本必须能容忍新增表；失败时不切流量。
- [ ] 发布后观测 provider error、cold start、command exit/unknown、CPU 结算、artifact 收集和用户级容量，不把 OpenSandbox metrics 当账单源。
- [ ] rollback 不执行 `down -v`、不删除 sandbox volume、不恢复 raw Pi built-ins、不影响 AF3/CORAL worker。
- [ ] 脚本拒绝未通过 Staging manifest、digest 不一致、runtime 不一致或存在未处理 unknown attempt 的发布。
- [ ] Commit: `feat(deploy): add opensandbox cutover and rollback`。

### Task C5: Staging 验收、管理员灰度和交接

**Files:**
- Create: `docs/releases/2026-10-09-opensandbox-gvisor-rollout.md`
- Modify: `docs/DEV_CLOUD_CONTEXT.md`
- Modify: `deploy/agent/OPENSANDBOX.md`

**Interfaces:** 发布记录保存非秘密的版本/digest、runtime 证据、验证范围、失败项、灰度用户范围、回退命令和当前状态。

- [ ] 用户明确要求验证后，在 Staging 执行 C2 smoke，保存真实 capability report 和各故障注入结果。
- [ ] Staging 通过后，由用户授权发布时把完全相同 manifest 部署到生产，先只为管理员账号开放文件工具。
- [ ] 单独开启 command 工具并观察；之后再扩大 allowlist，任何异常先关闭 command，再按需关闭全部 workspace。
- [ ] 确认普通聊天、模型流式、AF3/CORAL、文件上传和 artifact 下载未回归；未知项明确写入发布记录。
- [ ] 更新背景文档末尾交接：源码/制品版本、生产 provider、灰度范围、运行状态、回退位置和下一步。
- [ ] Commit: `docs(deploy): record opensandbox rollout evidence`。

## Goal Completion Gate

- [ ] 阶段 A、B、C 源码和部署制品完成，分批提交且未混入已有无关改动。
- [ ] 用户明确授权验证/发布后，Staging 真实 `runsc` 证据、端到端产物链路、故障关闭和卷保留全部有记录。
- [ ] 生产实际发布后才能把 Goal 标记 complete；只完成代码或计划时保持 active。
- [ ] 如果固定 OpenSandbox release 无法证明 Session mount namespace 或模型进程隔离，生产只开放安全通过的文件能力，命令能力维持关闭并在交接中说明。

