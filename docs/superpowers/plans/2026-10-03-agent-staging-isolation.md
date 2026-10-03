# 新版 PSKit 独立 Staging 环境实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在阿里云按需启动与生产隔离的完整 Staging，以合成数据验收同一发布制品，日常发布不迁移测试或生产用户数据。

**Architecture:** 保留生产 `stack.sh`；新增 Staging 专用配置生成器、三个 Compose 覆盖文件和 `staging.sh`。Staging 拥有独立 Supabase PG17、LiteLLM 和 Agent 项目，模型使用内网替身，AF3 使用 mock；宿主机 Nginx 只在 WireGuard 上提供相同 React `dist`。脚本在任何容器启动前解析最终 Compose 配置并拒绝生产项目、卷、网络、端口或密钥。

**Tech Stack:** Docker Compose、Python 3.12、Node 22（来自固定后端镜像）、Supabase、PostgreSQL 17、LiteLLM v1.100.3、FastAPI/Pi、Nginx、pytest。

**Spec:** `docs/superpowers/specs/2026-10-03-agent-staging-isolation-design.md`

## Global Constraints

- 三个项目固定为 `pskit-agent-supabase-staging`、`pskit-agent-litellm-staging`、`pskit-agent-staging`；共用且仅共用 `pskit-agent-supabase-staging_default` 网络。
- Supabase `127.0.0.1:18131`、Agent `127.0.0.1:18090`、LiteLLM `10.9.8.1:4002`、静态入口 `10.9.8.1:18132`；不使用 `0.0.0.0` 或公网 DNS。
- Staging Supabase 的 11 个固定容器名全部覆写；数据库卷、Storage 卷和 Agent 卷与生产不同。生产 `agent.bioailab.net`、`stack.sh`、4000 LiteLLM、A6000 接收器均保持原样。
- Staging 密钥、密码、DSN 和虚拟 key 必须新生成；私有目录 0700、文件 0600；不从生产 `.env`、数据库或 Pi 会话复制数据，不在日志或命令行泄露密钥。
- 后端固定镜像 ID、前端 `dist` 一次构建后供两环境使用；Staging AF3 只用 `mock` 且结果标记 `simulation=true`，模型别名 `claude-opus-4-8` 只指向隔离替身；LiteLLM 预算契约为团队 10 美元、每用户 2 美元 / 30 天。
- `staging.sh up|status|logs|down` 是唯一常规入口；`down` 保留所有卷。私网 HTTP 的 Staging Cookie `Secure=false`，生产继续 `Secure=true`。首次测试账号已确认邮箱、仅用 `@example.invalid`，不发邮件；重置数据必须独立显式执行。
- 所有提交只暂存本计划列出的路径；当前工作区已有其他未提交改动，不能顺手提交或覆盖。遵循用户已要求的 TDD：每个任务先写失败测试，再实现、验证、提交。

## File Structure

| 文件 | 单一职责 |
| --- | --- |
| `deploy/agent/scripts/prepare_staging.py`、`deploy/agent/scripts/staging_auth_keys.mjs` | 在全新私有目录生成 Staging 配置、Supabase JWT/JWKS 与各服务独立密钥；拒绝覆盖和不安全路径。 |
| `infra/supabase/compose.staging.yaml` | 覆写 Supabase 容器名、端口、显式卷和禁用注册邮件的配置。 |
| `infra/litellm/compose.staging.yaml`、`config.staging.yaml` | 在 Staging 网络内运行 LiteLLM 与模型替身。 |
| `deploy/agent/compose.staging.yaml` | 固定 Agent 镜像、网络、端口和 mock AF3；不启动回调代理。 |
| `deploy/agent/scripts/staging_preflight.py`、`deploy/agent/staging.sh` | 比较三个渲染后的 Compose 项目与生产禁用集合，检查资源，再有序启动、观察和停止。 |
| `deploy/agent/scripts/seed_staging.py` | 仅向 Staging Auth/API 写入幂等合成样本。 |
| `deploy/agent/host-nginx-agent-staging.conf`、`scripts/install_host_nginx_staging.sh` | WireGuard 私网 React `dist` 和 `/api/v1/` 入口；只安装独立 vhost。 |
| `deploy/agent/tests/test_staging_*.py`、`deploy/agent/tests/smoke_staging.py` | 静态隔离、失败路径、私网产品流程和生产数据不变的验证。 |

## Review Focus

1. **生产环境变量或卷名误传入 Staging：** Task 3 的预检测试必须在任何 `docker compose up` 前拒绝，且错误输出不包含密钥。
2. **生成密钥中途失败后再次执行：** Task 1 的测试必须证明没有半成品被当作有效配置，也不会覆盖已有文件或跟随符号链接。
3. **模型替身、Supabase 或 Agent 某服务启动失败：** Task 3 的假 Docker 测试必须证明后续服务不启动，生产服务不会被停止，Staging 卷保留。
4. **重复 seed 或脚本误指生产 API：** Task 4 的测试必须证明样本数量稳定，且非 `127.0.0.1:18131` / `127.0.0.1:18090` 目标会被拒绝。
5. **主机没有 WireGuard IP 或生产 Nginx vhost 已存在：** Task 5 的测试必须证明安装脚本拒绝切换，且不改写生产 vhost。

---

### Task 1: 生成独立且可恢复的 Staging 私有配置

**Files:** Create `deploy/agent/scripts/prepare_staging.py`, `deploy/agent/scripts/staging_auth_keys.mjs`, `deploy/agent/tests/test_prepare_staging.py`。私有配置只能放在仓库外的操作者目录，不新增仓库内 `.env`。

**Interfaces:** `prepare_staging(target_dir: Path, *, backend_image: str, frontend_dist: Path) -> None`；产出 `supabase.env`、`litellm.env`、`backend.env.base`、`admin.env`、`cloud.env`、`proxy.env`、`seed.env`、`manifest.json`，全部在仓库外的 `target_dir`。`backend.env.base` 缺少尚未由 LiteLLM 签发的虚拟 key；Task 3 才写 `backend.env`。`manifest.json` 仅含非秘密路径、项目名、镜像 ID 和 `dist` 哈希。Node 脚本从 stdin 读取新 JWT secret，向 Python 父进程 stdout 返回 JSON；stdout 只能被父进程捕获，不写日志。

- [ ] **Step 1: 写失败测试。** `test_generates_distinct_private_staging_secrets` 断言每个文件 0600、目录 0700、全部密钥不同于模板默认值，`JWT_KEYS`/`JWT_JWKS` 可配对且公共 JWKS 没有私钥 `d`；`test_existing_or_symlink_target_is_untouched` 断言拒绝覆盖；`test_mid_generation_failure_leaves_no_valid_manifest` 断言可恢复且没有有效半成品；`test_output_does_not_contain_secrets` 断言 stdout/stderr 不泄露。
- [ ] **Step 2: 运行 `python -m pytest deploy/agent/tests/test_prepare_staging.py -q`；预期因为函数缺失而失败。**
- [ ] **Step 3: 实现生成器。** 从 `infra/supabase/.env.example` 中仅复制非秘密配置，逐项生成强随机值、legacy HS256 JWT 和新 ES256/JWKS；用固定后端镜像内 Node 22 执行只读挂载的 `staging_auth_keys.mjs`，通过 stdin 传 JWT secret；以临时私有目录构建完整文件集后原子发布，`O_EXCL|O_NOFOLLOW` 拒绝覆盖；设置 `SITE_URL=http://10.9.8.1:18132`、`API_EXTERNAL_URL=http://10.9.8.1:18132/auth/v1`、`CLOUD_DISABLE_SIGNUP=true`、`ENABLE_EMAIL_AUTOCONFIRM=false`。不得调用现有会打印密钥并修改源码的 Supabase 脚本。
- [ ] **Step 4: 重跑该测试并运行 `git diff --check`；预期全通过且 `git status` 无私有配置。**
- [ ] **Step 5: 只暂存本任务文件并提交 `feat(deploy): generate isolated staging secrets`。**

### Task 2: 三个隔离 Compose 项目与模型替身

**Files:** Create `infra/supabase/compose.staging.yaml`, `infra/litellm/compose.staging.yaml`, `infra/litellm/config.staging.yaml`, `deploy/agent/compose.staging.yaml`, `deploy/agent/tests/test_staging_compose.py`。

**Interfaces:** 分别以 `-p pskit-agent-supabase-staging`、`-p pskit-agent-litellm-staging`、`-p pskit-agent-staging` 运行，依次叠加 Supabase `docker-compose.yml + compose.cloud.yaml + compose.staging.yaml`，LiteLLM `compose.shared-postgres.yaml + compose.staging.yaml`，Agent `compose.yaml + compose.cloud.yaml + compose.postgres.yaml + compose.staging.yaml`；变量取 Task 1 的私有文件。LiteLLM 的 `model-stub` 复用 `deploy/agent/tests/mock_model_gateway.py`，只在 Staging 网络。

- [ ] **Step 1: 写失败的 Compose 渲染测试。** `docker compose config --format json` 分别断言三个项目名、全部 11 个 `container_name` 后缀、独立数据库/Storage/Agent 卷、固定私网端口、无生产网络、无 PostgreSQL 对外端口；Agent 环境有 `RESEARCH_AGENT_AF3_EXECUTOR=mock`、`RESEARCH_AGENT_AUTH_COOKIE_SECURE=false` 且无 A6000 地址；LiteLLM 只连接 Staging PG17、`claude-opus-4-8` 指向 `model-stub`；`web` 和 `af3-callback-proxy` 不在默认启动服务列表。
- [ ] **Step 2: 运行 `python -m pytest deploy/agent/tests/test_staging_compose.py -q`；预期失败。**
- [ ] **Step 3: 实现三个覆盖文件及模型配置。** 对基础 Compose 固定名称和显式卷逐一覆写；Agent 使用 `AGENT_BACKEND_IMAGE` 与单独 `AGENT_PG_DATA_VOLUME`，只发布 `127.0.0.1:18090:8000`；LiteLLM 绑定 `10.9.8.1:4002`，替身服务使用同一固定后端镜像与现有 mock 脚本。
- [ ] **Step 4: 重跑 Compose 测试，并用 `docker compose ... config --quiet` 验证三套配置；预期全通过。**
- [ ] **Step 5: 只暂存本任务文件并提交 `feat(deploy): isolate staging compose projects`。**

### Task 3: 安全预检和 Staging 启停入口

**Files:** Create `deploy/agent/scripts/staging_preflight.py`, `deploy/agent/staging.sh`, `deploy/agent/tests/test_staging_stack.py`。

**Interfaces:** `validate_staging(manifest: Path, rendered: dict[str, dict], production_fingerprints: dict[str, str]) -> None` 拒绝生产项目/卷/网络/端口/DSN/密钥；生产指纹从当前云端生产 env 只读计算，不复制其值进 Staging。`staging.sh up|status|logs <supabase|litellm|agent>|down` 读取 Task 1 文件。`up` 调用现有 `provision_shared_postgres.py`、`migrate_postgres` 和 `infra/litellm/bootstrap_pskit.py --base-url http://10.9.8.1:4002 --env-file ... --key-file ...`；虚拟 key 写入私有文件，随后从 `backend.env.base` 原子创建 `backend.env` 供 Agent 使用。

- [ ] **Step 1: 写失败测试。** 用假 Docker 记录调用，断言预检先于任何 `up`，随后按 Supabase→数据库角色/迁移→LiteLLM/替身→预算和 key→Agent 顺序启动；注入生产 DSN、网络、卷、端口、key 或相同镜像 tag 但不同 ID 时拒绝；`docker image inspect`、内存/磁盘或任一服务失败时不继续；`down` 仅对三个 Staging 项目执行且没有 `-v`。
- [ ] **Step 2: 运行 `python -m pytest deploy/agent/tests/test_staging_stack.py -q`；预期失败。**
- [ ] **Step 3: 实现预检与脚本。** 只接受固定的三个 `-p`；启动前用 `backend.env.base` 完成第一次隔离预检，签发虚拟 key 后用 `backend.env` 完成第二次隔离预检；读取并比较渲染 JSON、私有文件和生产配置指纹；检查 `docker image inspect --format '{{.Id}}'` 等于 manifest 锁定 ID、可用内存至少 4 GiB、磁盘至少 10 GiB、端口空闲；失败时保留 Staging 卷，绝不调用生产 `stack.sh`。`status` 显示三项目状态和 Staging PG17 数量，`logs` 不打印 env。
- [ ] **Step 4: 重跑本任务测试和 Task 2 Compose 测试；预期全通过。**
- [ ] **Step 5: 只暂存本任务文件并提交 `feat(deploy): add fail-closed staging launcher`。**

### Task 4: 幂等合成数据及私网冒烟验证

**Files:** Create `deploy/agent/scripts/seed_staging.py`, `deploy/agent/tests/test_seed_staging.py`, `deploy/agent/tests/smoke_staging.py`, `deploy/agent/tests/test_smoke_staging.py`。

**Interfaces:** `seed_staging(auth_url: str, api_url: str, supabase_env: Path, credentials_file: Path) -> dict[str, str]` 仅允许任务规定的 loopback 目标；从 Staging `supabase.env` 读取服务端管理 key，通过 Supabase Auth 管理接口创建/确认 `staging-user@example.invalid`，再经 Python API 创建一个项目、会话、少量文本文件、Token/GPU 配额样本。`smoke_staging.py` 通过 WireGuard 页面 API 完成登录、SSE、配额、模型计量、AF3 `simulation=true` 和 `/internal/` 拒绝检查；生产行数/账本基线与收尾快照必须一致。

- [ ] **Step 1: 写失败测试。** HTTP 替身记录请求，断言两次 seed 后各对象仅一个、现有对象不改写；生产 URL 和私网 4000/4001 URL 均拒绝；冒烟脚本断言 Auth/Agent 流程与生产前后计数相等，断言没有 AF3 claim 到 `10.9.8.2`。
- [ ] **Step 2: 运行 `python -m pytest deploy/agent/tests/test_seed_staging.py deploy/agent/tests/test_smoke_staging.py -q`；预期失败。**
- [ ] **Step 3: 实现 seed 和 smoke。** 账号密码只读取 0600 文件；以稳定名称查询后创建，API 调用带幂等键；上传限定少量文本；失败仅报告状态和路径，不回显 token/密码。生产快照只读、分别采 `auth.users`、`pskit`、`litellm` 行数与账本摘要。
- [ ] **Step 4: 重跑本任务测试；预期全通过。**
- [ ] **Step 5: 只暂存本任务文件并提交 `feat(deploy): seed and smoke isolated staging`。**

### Task 5: WireGuard 静态入口和发布手册

**Files:** Create `deploy/agent/host-nginx-agent-staging.conf`, `deploy/agent/scripts/install_host_nginx_staging.sh`, `deploy/agent/tests/test_staging_nginx.py`, `deploy/agent/STAGING.md`。

**Interfaces:** root 安装脚本只写 `/etc/nginx/conf.d/agent-staging-private.conf` 和独立静态目录 `/var/www/agent-staging`，从 manifest 校验的同一 `dist` 安装；不写生产 `/etc/nginx/conf.d/agent.bioailab.net.conf`。私网 `10.9.8.1:18132` 提供 SPA 与 `/api/v1/` 到 `127.0.0.1:18090`。

- [ ] **Step 1: 写失败测试。** 断言 Nginx 仅 `listen 10.9.8.1:18132`，`/api/v1/` 反代 localhost Staging，`/internal/` 与 Supabase 管理接口被拒绝，SPA 路由回退 `index.html`；假 root 测试证明无 WireGuard IP、同名 vhost 已存在或 `nginx -t` 失败时不改生产文件且可恢复原 Staging vhost。
- [ ] **Step 2: 运行 `python -m pytest deploy/agent/tests/test_staging_nginx.py -q`；预期失败。**
- [ ] **Step 3: 实现独立 vhost、安装脚本和手册。** 手册列明先构建一次并锁定镜像 ID/dist 哈希，再生成配置、`up`、seed、私网冒烟、同制品发布生产、结构迁移；明确旧 SQLite→PG 数据迁移只运行一次，后续发布不复制用户数据；列出停止命令、root 安装步骤、资源门槛与回退。
- [ ] **Step 4: 重跑本任务测试、全部 `deploy/agent/tests/test_staging_*.py`、`docker compose config --quiet` 和 `git diff --check`；预期全通过。**
- [ ] **Step 5: 只暂存本任务文件并提交 `docs(deploy): document private staging release flow`。**

### Task 6: 阿里云 Staging 实例验收

**Files:** Modify `deploy/agent/STAGING.md` only for 实际部署时发现的命令差异；无需改生产配置。

**Interfaces:** 使用 Tasks 1–5 的脚本和用户已指定的阿里云主机；root 操作仅通过已审阅的独立安装脚本，由具备 root 权限的一方执行。

- [ ] **Step 1: 记录生产只读基线。** 保存生产容器列表、生产 `auth.users`、`pskit` schema、`litellm` 数据库的计数与账本摘要、生产健康状态；检查内存/磁盘/端口。
- [ ] **Step 2: 部署并验证。** 传输同一固定后端镜像和 `dist`，运行 `prepare_staging.py`、`staging.sh up`、`seed_staging.py`；在执行 root Nginx 安装前完成私网 API 冒烟，安装后从 WireGuard 访问 `http://10.9.8.1:18132` 跑完整 smoke。
- [ ] **Step 3: 检查 Staging `down` 后生产计数与健康仍等于基线，确认没有真实 AF3 claim；如失败仅停止 Staging 并保留卷/日志。**
- [ ] **Step 4: 如需记录修订，只暂存 Staging 手册并提交；报告实际验证结果、私网地址和任何尚需用户 root 操作的步骤。**
