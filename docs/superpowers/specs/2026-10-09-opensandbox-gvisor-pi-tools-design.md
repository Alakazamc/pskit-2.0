# OpenSandbox、gVisor 与 Pi 工作区工具设计

日期：2026-10-09（Asia/Shanghai）

## 1. 目标

PSKit 面向公网用户提供文件读写、代码执行和产物生成能力时，不能让模型驱动的工具直接访问生产后端容器。目标架构使用开源 OpenSandbox 管理执行环境，使用 gVisor `runsc` 隔离宿主内核，并把 Pi 暴露给模型的文件与命令工具代理到用户沙箱。

完成后应满足：

- 每个用户拥有一个独立沙箱和持久工作卷，同一用户的多个会话复用该信任边界。
- 用户进程无法看到宿主机或 PSKit 后端的根文件系统、源码、数据库网络和长期凭据。
- Pi 的 `read`、`write`、`edit`、目录搜索和命令执行只作用于该用户的 `/workspace`。
- 上传文件进入对应会话目录；生成文件从本轮产物目录回收并转成已有 Rich UI 文件卡片。
- OpenSandbox、gVisor、execd、基础镜像和 SDK 使用固定版本或镜像 digest，生产不使用 `latest`。
- 运行时缺少 gVisor、认证、私网或隔离能力时失败关闭，不自动回落到普通 `runc` 或本地执行。

## 2. 当前事实与风险

生产后端没有设置 `RESEARCH_AGENT_PI_EXECUTION`，当前源码默认值是 `local`。生产也没有运行 `sandbox-manager`、`sandbox-gateway` 或 `pskit-sbx-*` 用户容器；阿里云 Docker 只注册了 `runc`。因此当前 Pi 运行在后端容器内。

仓库已有的 `compose.sandbox.yaml` 是可选的自研 Docker provider，并未部署。它通过非 root UID、只读根、capability 清空、资源限制和 internal 网络降低风险，但普通 `runc` 仍与宿主共享内核；只读根只阻止写入，不阻止读取容器内的 `/app`、`/etc`、`/proc`。仓库文档也明确规定其任意 shell、Python 和 Pi 内置文件工具仍关闭。

所以在现状下直接删除 Pi 的 `--no-builtin-tools` 会把后端容器文件系统暴露给模型，不能作为本功能的实现。

## 3. 方案选择

### 3.1 采用：OpenSandbox 控制面 + gVisor 执行层

OpenSandbox 提供生命周期、命令、文件、流式日志和资源观测 API；Docker 单机模式适合当前阿里云部署。OpenSandbox 的 secure runtime 配置支持 gVisor，并能在指定 OCI runtime 不存在时拒绝启动。gVisor 通过 `runsc` 在工作负载和宿主 Linux 内核之间增加用户态应用内核。

OpenSandbox 本身不是隔离强度的证明。必须同时满足：

- `[secure_runtime].type = "gvisor"`；
- Docker runtime 固定为 `runsc`；
- OpenSandbox 启动预检和 PSKit readiness 都确认实际 runtime；
- 创建后的每个沙箱通过 inspect 或诊断信息证明使用 `runsc`；
- 任一检查失败时停止接收新的 Agent Run。

### 3.2 不采用：直接启用 Pi 原始内置工具

Pi 原始文件工具接受绝对路径和相对路径；设置 `cwd` 不能构成文件系统边界。原始 shell 也可以绕过应用层路径检查。因此不能在后端或普通 Docker 容器内直接启用。

### 3.3 不采用：只给现有 Docker manager 增加路径判断

路径判断适用于文件 API，但无法约束任意 shell、解释器或原生程序，也没有增加宿主内核隔离。现有 manager 可在迁移期保留用于回退和数据导出，但不再作为公网代码执行的目标 provider。

### 3.4 暂不采用：Kubernetes、Kata 或 Firecracker

当前为单机 Docker 部署，尚未核验 ECS 的 KVM、嵌套虚拟化和 Kubernetes 运维条件。OpenSandbox provider 接口保留以后替换运行时的可能，本轮不引入第二套调度平台。

## 4. 目标架构

```text
Browser
  │
  ▼
Python API / AgentService
  ├── Supabase 身份、会话所有权、配额和 Run
  ├── Pi RPC：推理、transcript、工具选择
  └── OpenSandboxProvider
          │  私有 API key + 私有网络
          ▼
    OpenSandbox Server
          │  Docker runtime = runsc
          ▼
    每用户一个 gVisor Sandbox
      ├── execd：文件、命令、流式日志、进程状态
      ├── 最小且无密钥的运行时镜像
      └── /workspace：该用户的持久卷
```

Pi RPC 第一阶段继续运行在受信任的 Python 后端执行域。模型不能直接调用本机文件系统；Pi extension 注册与原生工具相同的语义入口，但实现调用 PSKit 内部工具 API，Python 再调用 OpenSandbox SDK。

这样可以保留现有 Pi transcript、流式模型事件和自动唤醒，不需要先迁移历史会话；同时 OpenSandbox API key、execd token、真实模型 key和数据库凭据都不会交给模型或写入用户工作区。

## 5. 信任边界

| 组件 | 可以访问 | 明确不能访问 |
| --- | --- | --- |
| 浏览器 | 用户 API、自己的文件和产物 | OpenSandbox、execd、数据库和服务密钥 |
| Python/Pi 控制面 | 身份、Run、配额、OpenSandbox provider | 不执行模型生成的任意 shell |
| OpenSandbox Server | Docker lifecycle、指定工作卷、固定镜像 | 公网入口、Supabase service role、LiteLLM master key |
| 用户沙箱 | 自己的 `/workspace`、最小运行时、受控出口 | 宿主目录、后端源码、Docker socket、数据库网络、其他用户卷 |
| A6000/4090 计算服务 | 已审核 Job 输入、模型文件与计算资源 | 用户沙箱卷、聊天 JWT、平台管理凭据 |

同一用户的多个会话属于同一个 OS 信任边界。这是“一用户一沙箱、多个会话复用”的既定产品选择；会话目录用于组织、并发控制和产品授权，不声称能防御同一用户自己的另一个会话。不同用户不得共享沙箱、卷、execd token 或网络身份。

## 6. 文件系统语义

用户工作卷只挂载到沙箱的 `/workspace`：

```text
/workspace/
├── sessions/<session_id>/
│   ├── files/                         已授权上传文件，只读
│   ├── work/                          会话持久草稿，可读写
│   ├── attempts/<attempt_id>/         当前执行临时目录，可读写
│   └── artifacts/<attempt_id>/        本轮正式产物，可读写
└── shared/                             用户主动共享的文件，首版不自动写入
```

“只能读 workspace”的精确定义是：

1. 用户数据只存在于 `/workspace`，宿主机和后端文件系统不挂载进沙箱。
2. Pi 文件类工具只接受相对于 `/workspace` 的逻辑路径；拒绝绝对路径、`..`、NUL、设备文件和 symlink 逃逸。
3. 沙箱命令必须读取 Python、Node、shell、动态库等最小运行时文件。该只读镜像中不得包含 PSKit 后端源码、部署配置和秘密；读取这些无密钥运行时文件不视为读取服务器根文件系统。
4. `/proc` 只提供运行所需的最小视图；不得暴露其他沙箱或控制进程的 `environ`。是否采用 OpenSandbox 当前的 isolated execution/Landlock 能力，以固定版本的能力探测为准；其状态为不可用或 fail-open 时不能作为上线依据。
5. 文件上传由 Python 校验真实所有权和摘要后写入 `files/`。模型提供的文件名不决定目标路径。
6. 产物只从当前 `artifacts/<attempt_id>/` 回收；拒绝 symlink、目录、越界、超出单文件和单轮数量/容量限制的输出。

基础镜像采用最小、固定 digest 的 PSKit sandbox image，只包含 execd 所需入口和经批准的运行时。镜像不得复制生产 `.env`、SSH 配置、云凭据、Git 凭据或后端数据。

execd 与用户进程使用不同身份：execd 作为沙箱内受信任 supervisor，所有模型驱动的命令固定以无 capability 的 UID/GID `10001:10001` 运行。用户不能覆盖 UID/GID、reserved env 或启动参数；`ptrace`、提权和读取 `/proc/1/environ` 必须被拒绝。若选定 OpenSandbox release 不能让这些检查失败关闭，阶段 A 不得通过，需固定补丁版本或更换隔离实现。

## 7. Pi 工具模型

Pi 仍以 `--no-builtin-tools` 启动。PSKit extension 注册以下受控工具，名称和参数尽量保持 Pi 用户体验，但不复用本机实现：

| 工具 | 后端行为 |
| --- | --- |
| `read` | OpenSandbox 文件 API 分段读取 `/workspace` 内文本或受支持文件 |
| `write` | 只写 `work/`、`attempts/<attempt_id>/` 或 `artifacts/<attempt_id>/` |
| `edit` | 带原内容匹配和 revision/digest 检查的原子修改 |
| `ls` / `find` / `grep` | 通过受限文件 API 或沙箱内固定程序执行，结果限制数量和字节数 |
| `bash` | 通过 execd 在当前 attempt 目录执行，带时间、输出、PID、CPU和内存上限 |
| `python` | 通过 execd 的 argv/code API执行，不在后端 `subprocess` |

工具调用携带 `run_id`、`attempt_id`、`session_id` 和短期 agent tool token。Python 每次调用重新验证：用户、会话、Run 状态、并发、配额、沙箱 owner 和允许的操作。浏览器不能提交 sandbox ID、Docker 参数、UID/GID、镜像或任意挂载。

命令输出以真实增量事件转成已有 tool/progress part；取消请求通过 execd 终止进程并等待确认。进程失联时 Run 进入 unknown/failed，不宣称已经停止或释放用量。

## 8. 生命周期与持久化

PSKit PostgreSQL 保存 `user_id → sandbox_id → workspace_volume → provider_revision` 映射及活动 lease。OpenSandbox sandbox ID 是可替换的运行实例，不是用户长期身份；用户卷独立于实例生命周期。

- 首次需要文件或命令工具时创建沙箱，不因登录或查看历史会话启动。
- 同一用户并发创建必须收敛到一个已确认实例。
- 活跃 attempt 定期续租；只有 execd 已确认所有进程终态后才能释放 lease。
- 空闲时停止或销毁运行实例，保留工作卷和数据库映射。
- 镜像升级先 drain，拒绝新 attempt，等待活动执行完成，再用同一卷重建。
- OpenSandbox 或 gVisor 不健康时，聊天仍可使用不依赖本地执行的模型和远程 MCP；需要 workspace 的工具明确返回暂时不可用，不回落到后端本地执行。

Pi transcript 继续由后端现有存储负责。工作区停机或重建不影响对话历史；长时 AF3/CORAL Job 仍由中央 Job/receipt/outbox 负责，不依赖用户沙箱进程存活。

## 9. 网络与凭证

- OpenSandbox Server、execd 和沙箱端口不发布到公网，仅 Python 后端所在私网可访问。
- OpenSandbox Server 必须配置 API key；无 key 的 insecure 模式禁止上线。
- execd 使用独立访问 token，由 provider 保管，不进入 Pi prompt、transcript、工具结果或工作区。
- 沙箱默认无任意公网出口。模型、MCP、AF3/CORAL 均经 PSKit 受控网关和短期 Run token访问。
- 如以后开放 `pip`、公开数据库或用户 MCP，使用 OpenSandbox egress policy/sidecar 明确 allowlist；DNS、IPv4、IPv6、重定向和私网地址均需校验。
- 不允许沙箱访问 Docker socket、OpenSandbox Server、manager、数据库、Supabase、LiteLLM 管理入口或其他沙箱的 execd。

## 10. 资源、计量与额度

每个用户沙箱保留现有默认资源档位：1 CPU、1 GiB RAM、256 PIDs；磁盘容量和 inode 必须增加硬限制，不能只依赖上传 API。实际生产值由管理员配置，浏览器不得覆盖。

每次 command/code attempt 记录 wall time、退出码、超时、取消和 OpenSandbox 可提供的 CPU/RAM观测。用户每日 CPU 核毫秒仍由 PSKit 配额账本预占和结算；OpenSandbox metrics 是测量来源，不成为第二个额度真相源。远程 GPU Job 沿用已有计算服务的 GPU 用量协议。

输出日志、单条工具结果和产物均设置字节上限；超过限制时截断显示并保留结构化错误，不能把无限 stdout 塞进 Pi context。

## 11. 错误处理和降级

| 故障 | 对用户行为 |
| --- | --- |
| OpenSandbox Server 不可用 | workspace 工具返回可重试错误；普通无工具聊天可继续 |
| `runsc` 未安装或未被实际使用 | readiness 失败；禁止创建或执行沙箱 |
| 固定镜像拉取失败 | 不采用其他 tag，不回落本地，记录 provider 错误 |
| execd token/owner 不匹配 | 拒绝请求并审计，不自动创建第二个冲突实例 |
| 命令超时或取消 | 等待终止确认，保存已产生的日志和用量 |
| 工作卷或磁盘满 | 拒绝新写入，保留已有文件和可下载产物 |
| 产物越界、symlink 或超限 | 跳过该产物并生成结构化安全错误 |
| provider 版本变更 | drain 后替换；不原地更改活跃沙箱 |

## 12. 分阶段交付

### 阶段 A：OpenSandbox + gVisor 基础设施和 provider

- 固定 OpenSandbox server/SDK/execd、gVisor 和 sandbox image 版本。
- 新增 `OpenSandboxProvider`，实现 ensure、exec、files、metrics、cancel、stop 和 replace。
- 本地隔离环境验证真实 `runsc`、跨用户卷隔离、无宿主/后端挂载、私有网络和失败关闭。
- 此阶段不改变生产 Pi 工具集合。

### 阶段 B：Pi workspace 工具与产物闭环

- 注册受控的文件、搜索、命令和 Python 工具。
- 对接上传同步、attempt 目录、实时输出、取消、配额与产物回收。
- Staging 验证“上传 → 读取 → 生成 Markdown/CSV/DOCX → 文件卡片下载”的完整链路。

### 阶段 C：生产切换与旧 provider 退役

- 先在 Staging 持续运行，验证冷启动、并发、空闲回收、故障恢复和资源上限。
- 生产安装并注册固定版本 `runsc`，先只开放管理员测试账号，再逐步开放正式用户。
- 保留关闭 workspace 工具的即时回退开关；回退不删除用户卷。
- 稳定后移除自研 Docker manager 的生产入口，代码删除另做独立变更。

三个阶段分别形成实施计划和可回退提交。阶段 A 验收前不得实现或启用阶段 B 的任意命令执行入口。

## 13. 验收标准

1. 生产或 Staging 的实际沙箱 inspect 显示 OCI runtime 为 `runsc`；只有 `runc` 时创建请求失败。
2. 两个用户的工作卷、sandbox ID、execd token和进程不可互访；同一用户多个会话按预期共享用户级 workspace 边界。
3. 沙箱内无法读取宿主机路径、后端 `/app`、后端环境变量、Docker socket和数据库网络。
4. Pi 的文件工具拒绝绝对路径、`..`、symlink 和跨用户路径；命令工具不能在后端容器执行。
5. 上传文件可以读取；生成产物被收集为结构化 artifact/file part，并在聊天中显示预览或下载卡片。
6. 流式 stdout/stderr、停止按钮、超时、取消和进程退出状态来自真实 execd 事件，不用前端模拟。
7. 空闲实例停止或替换后，用户卷和会话文件仍存在；活动任务不会被空闲回收误杀。
8. OpenSandbox、gVisor 或网关故障时不会回落到 local Pi shell，已有普通聊天和远程持久 Job保持可用。
9. 安全日志不包含 OpenSandbox API key、execd token、模型 key、JWT 或文件正文。
10. Staging 通过后才允许生产灰度；发布记录包含版本、digest、runtime 证据、验证范围和回退方法。

## 14. 非目标

- 本轮不把 AF3、CORAL 或其他 GPU 模型搬进用户沙箱。
- 不让浏览器直接访问 OpenSandbox 或自行选择镜像、资源和挂载。
- 不允许用户安装任意系统包或长期守护进程；公开依赖安装策略以后单独设计。
- 不承诺同一用户的不同会话互为恶意租户。
- 不把 OpenSandbox metrics 当成新的账单数据库。
- 不在首次切换中删除旧 manager、旧卷或历史 Pi transcript。

## 15. 上游依据

- OpenSandbox 架构和 SDK：<https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/architecture/index.md>
- OpenSandbox Python SDK：<https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/sdks/python.md>
- OpenSandbox API：<https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/api/index.md>
- OpenSandbox secure runtime：<https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/guides/secure-container.md>
- OpenSandbox server 配置：<https://github.com/opensandbox-group/OpenSandbox/blob/main/server/configuration.md>
- gVisor 架构：<https://gvisor.dev/docs/architecture_guide/intro/>
- gVisor 安装和 Docker runtime：<https://gvisor.dev/docs/user_guide/install/>

上游 `main` 文档仅用于设计依据。实施时选择一个固定 release，核对 release 对应的 server、SDK、execd 和镜像兼容矩阵，并以锁文件、镜像 digest 和实际能力探测为准。
