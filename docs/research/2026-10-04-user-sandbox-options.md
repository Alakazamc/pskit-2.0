# 每用户沙箱、Pi RPC 与计算服务边界调研

日期：2026-10-04。所有外部链接均于本日读取；仅采用官方文档和项目源码。本文交付对接建议，不启用沙箱、不修改后端、不运行测试、不部署。外部项目的 `main` 文档是调研快照，不能代替将来选定版本的验收。

## 结论

建议采用 **每用户一份持久工作区，按需启动一个 CPU 沙箱，同一用户的多个会话复用沙箱**。每个会话启动独立 Pi RPC 子进程，使用独立 transcript 和当前工作目录。容器空闲后停止，文件保留；再次请求时冷启动即可。长期身份与存储稳定，不必承诺一个容器实例永远不变。

GPU 模型服务继续独立运行在 A6000 等计算主机，由 Python 的计算入口、配额与任务服务统一接入。CPU 沙箱发请求并消费结果，模型权重和 GPU 运行时留在计算服务，不为每个用户重复加载。

两条有意义的选择：

- **先满足当前课题组内使用**：收敛现有 Docker manager 的接口，补齐生命周期、出口网络、文件同步与计量。独立的 rootless Docker daemon 可减少管理器失陷后的宿主权限，但它仍共享内核，且现有 rootful Compose 网络不能直接跨 daemon 复用。
- **准备让公网用户运行代码、shell 或第三方 Skill**：优先评估 OpenSandbox 的 Docker 控制面与 SDK，执行层使用 gVisor；也可保留现有 manager 接口、底下改用 `runsc`。不要把普通 Docker 容器或工作目录当成完整的强隔离保证。gVisor 同样要求外部网络和资源策略。[Docker rootless](https://docs.docker.com/engine/security/rootless/)、[Docker Engine 安全模型](https://docs.docker.com/engine/security/)、[gVisor 安全模型](https://gvisor.dev/docs/architecture_guide/security/)、[OpenSandbox 控制面](https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/components/server.md)

本文暂按“同用户多会话复用，不同用户隔离”解释原需求。如果实际希望不同用户共享容器，不能直接沿用下面的租户边界；需先明确成员间是否共享文件、进程和凭据。

## 1. 当前仓库实际做到哪里

| 事实 | 源码证据 | 实际边界 |
| --- | --- | --- |
| Pi 执行有 `local` 与 `sandbox` 两种模式，默认 `local` | [config.py](/home/jhli/pskit-2.0/new_backend/app/config.py:51)、[main.py](/home/jhli/pskit-2.0/new_backend/app/main.py:140) | 有沙箱代码不等于生产已开启；默认模式是在后端容器内启动 Pi。 |
| 沙箱是可选 Compose overlay | [compose.sandbox.yaml](/home/jhli/pskit-2.0/deploy/agent/compose.sandbox.yaml:1) | 只有叠加该文件才覆盖执行模式并启动 manager。 |
| 容器与卷由环境 namespace 和用户 ID 的哈希确定，每用户复用 | [sandbox_manager.py](/home/jhli/pskit-2.0/new_backend/app/sandbox_manager.py:145) | 每用户一个命名卷；跨生产、Staging 要采用不同 namespace。 |
| 管理器启动 bridge，Pi 在 bridge 内按请求启动 | [sandbox_manager.py](/home/jhli/pskit-2.0/new_backend/app/sandbox_manager.py:180)、[sandbox_bridge.py](/home/jhli/pskit-2.0/new_backend/app/sandbox_bridge.py:115) | 不是所有会话共享一个有状态 Pi 进程。容器中长期运行的是 bridge。 |
| Pi 的工作目录和 transcript 根是 `session_dir/session_id` | [pi_rpc.py](/home/jhli/pskit-2.0/new_backend/app/adapters/live/pi_rpc.py:83)、[pi_rpc.py](/home/jhli/pskit-2.0/new_backend/app/adapters/live/pi_rpc.py:159) | 同容器、同 UID 的进程可以访问其他会话目录，目录只是数据组织方式。 |
| 使用非 root UID、只读根文件系统、临时目录、CPU/内存/PIDs 限制 | [sandbox_manager.py](/home/jhli/pskit-2.0/new_backend/app/sandbox_manager.py:184) | 当前为 UID 10001、1 CPU、1 GiB、256 PIDs；没有工作卷磁盘容量上限，也没有按会话累计 CPU 用量账本。 |
| manager 持有 Docker socket，用户沙箱不持有 | [compose.sandbox.yaml](/home/jhli/pskit-2.0/deploy/agent/compose.sandbox.yaml:33) | manager 是高权限基础设施；只读根、去 capability 不会消除通过 socket 控制宿主 Docker 的权限。 |
| 沙箱接入 `app` 网络，并要求其为 `internal` | [sandbox_manager.py](/home/jhli/pskit-2.0/new_backend/app/sandbox_manager.py:160)、[compose.yaml](/home/jhli/pskit-2.0/deploy/agent/compose.yaml:79) | 没有公网默认路由不代表只能访问一个 API；沙箱仍与该网络其他容器连通，包含 manager、backend 与其他用户 bridge。 |
| 30 分钟无新 `ensure` 请求后停止容器，卷保留 | [sandbox_manager.py](/home/jhli/pskit-2.0/new_backend/app/sandbox_manager.py:67)、[sandbox_manager.py](/home/jhli/pskit-2.0/new_backend/app/sandbox_manager.py:229) | 时间戳不是活动 Run 心跳。没有活动引用计数；以后允许长时间本地执行时，会有正在运行却被回收的风险。 |
| 内置读写、shell、外部 Skill 自动发现均关闭 | [pi_rpc.py](/home/jhli/pskit-2.0/new_backend/app/adapters/live/pi_rpc.py:140) | 现在只是已注册的 PSKit 工具，不是任意代码执行环境。 |
| 已上传文件未同步为沙箱文件 | [SANDBOX.md](/home/jhli/pskit-2.0/deploy/agent/SANDBOX.md:10) | 不能向用户声称“上传到 workspace 后 Pi 已可以在磁盘读取”。 |
| 会话执行已有锁、持久 Run lease 和全局/用户并发配置 | [agent.py](/home/jhli/pskit-2.0/new_backend/app/services/agent.py:288)、[config.py](/home/jhli/pskit-2.0/new_backend/app/config.py:54) | 容器资源限制、会话串行与用户并发额度是三个不同控制层。 |

线上状态证据：2026-10-04 根执行代理通过 SSH 对阿里云后端做了白名单字段的只读检查，镜像为 `pskit-agent-backend:20261004-composer-8dce6b8b`；`RESEARCH_AGENT_PI_EXECUTION` 未设置、`PSKIT_SANDBOX_MANAGER_URL` 未配置，`RESEARCH_AGENT_MCP_EXECUTOR=disabled`。未设置执行模式与源码默认值合推，生产采用 **local Pi**。这是运行配置读值与代码默认值的结论，不是沙箱运行验收。最近的 [流式 UI 发布记录](/home/jhli/pskit-2.0/deploy/agent/releases/2026-10-04-reply-loading.md:21)也保留该后端镜像并明确未重启生产后端。本轮没有变更线上配置或创建沙箱。

## 2. 五种有意义的选项

这些产品不处于同一层：Docker/gVisor/Kata 主要解决运行与隔离；OpenSandbox/E2B 还提供生命周期、文件和执行 API。API 平台可以使用更强运行时，两者可组合。

| 方案 | 隔离与 Python 对接 | 持久化和休眠 | 对本项目的判断 |
| --- | --- | --- | --- |
| **Docker / rootless Docker** | Docker Engine API 可由 Python SDK 或现有 HTTP adapter 使用；rootless 使 daemon 与容器运行在非 root 用户命名空间，仍共享宿主内核。[官方说明](https://docs.docker.com/engine/security/rootless/) | 命名卷生命周期独立于容器；`stop/start` 保文件、不保进程内存。[卷](https://docs.docker.com/engine/storage/volumes/)、[stop](https://docs.docker.com/reference/cli/docker/container/stop/) | 最低对接成本，适合当前有限工具和受信课题组；公网任意执行应加更强运行时。rootless 必须核验 cgroup v2、systemd 与 controller delegation，否则资源限制可能被忽略。[资源限制条件](https://docs.docker.com/engine/security/rootless/tips/#limiting-resources) |
| **gVisor (`runsc`)** | OCI 运行时，以应用内核隔开用户代码与宿主内核；可由 Docker 指定运行时，生命周期仍由 manager 负责。[安全介绍](https://gvisor.dev/docs/architecture_guide/intro/)、[Docker 对接](https://gvisor.dev/docs/user_guide/quick_start/docker/) | 继续采用用户卷和停止/重新启动；不要求为 Pi 使用内存快照。 | 最值得评估的公网 CPU 执行隔离层。需要验证 Pi/Node、Python、文件与网络兼容性，不能凭能启动镜像就声称验证完成。 |
| **Kata Containers** | 每个 sandbox 以轻量 VM 运行独立 guest kernel，增强 namespace 容器的边界。[虚拟化设计](https://github.com/kata-containers/documentation/blob/master/design/virtualization.md) | 持久数据外置；VM 内存恢复取决于实际 hypervisor 与配置，不能一概承诺。 | 适合更严格租户隔离和已有虚拟化基础设施；当前 ECS 是否提供 KVM/嵌套虚拟化未核验。官方要求裸金属或 nested virtualization，不宜直接列为“即装即用”。[安装前置条件](https://github.com/kata-containers/kata-containers/blob/main/docs/install/README.md) |
| **OpenSandbox** | 官方 Python SDK、FastAPI lifecycle server，Docker/Kubernetes runtime；另有 execution/files API 与 MCP。Apache-2.0。[仓库](https://github.com/opensandbox-group/OpenSandbox)、[执行协议](https://github.com/opensandbox-group/OpenSandbox/blob/main/specs/execd-api.yaml) | 支持卷、TTL 与不同运行时的 pause/resume。Docker pause 是冻结进程；Kubernetes 默认 pause 是 rootfs 快照重建，不保进程内存。[架构](https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/architecture/index.md)、[pause/resume](https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/guides/pause-resume.md) | 本项目最匹配的现成自建 SDK 候选，可先用 Docker，不要求引入 K8s。不会替我们完成用户配额、会话 Run 与科研模型记账；这些仍归 PSKit。 |
| **E2B** | 托管 API/SDK；沙箱采用 Firecracker microVM。完整 runtime 公开，包含控制面和执行节点。[安全边界](https://e2b.dev/security)、[runtime](https://github.com/e2b-dev/runtime/blob/main/README.md) | 支持文件与内存快照恢复；托管有连续运行限制，断线后需要重新连进程接口。[持久化](https://docs.e2b.dev/sandbox/persistence) | 托管减少运维，自建也可评估，但官方架构含 Postgres、Redis、路由、模板构建、对象存储及监控存储，明显比现有单机 manager 更大。[架构](https://github.com/e2b-dev/runtime/blob/main/docs/ARCHITECTURE.md) 适合接受托管数据边界或需要独立内核快照的场景。 |

### Daytona 的时效变化

2026-10-04 查看的官方 `daytonaio/daytona` 仓库显示 **2026-10-03 已归档**。README 明确说明核心开发自 2026-06 转至私有代码库，公开仓库不再接受更新、修复与发布。当前云端文档仍有完整的生命周期、持久化和网络配置，但不能把这些能力自动算作可持续维护的开源自建版本。[官方仓库声明](https://github.com/daytonaio/daytona)、[当前持久化文档](https://www.daytona.io/docs/en/persistence/)

因此不将 Daytona 选为本项目的新开源基础；若选择其托管服务，需要以服务合同、区域、数据边界与当前 SDK 为依据。

## 3. Pi RPC 应怎样放进容器

Pi 的官方 RPC 是子进程上的 JSONL 控制协议。它解决跨语言调用、流式事件、会话与工具交互；不自动隔离不同用户，不提供 PSKit 的 GPU 调度。[RPC 文档](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/rpc.md)

本项目已锁定 `@earendil-works/pi-coding-agent` **0.87.1**，见 [package.json](/home/jhli/pskit-2.0/new_backend/pi/package.json:1)。后续对接按这个版本验证 RPC 与事件语义，不将 upstream `main` 的新特性直接加入部署承诺。

建议容器布局：

```text
用户沙箱（CPU，无 GPU，无上游真实 API key）
├── /opt/pi/                         固定版本 Pi、只读工具/Skill
├── bridge / execution agent         请求分派、子进程退出、心跳
└── /workspace/                      用户独占持久卷
    ├── shared/                      用户主动共享的文件/缓存
    └── sessions/<session_id>/
        ├── files/                   本会话授权文件
        ├── artifacts/               本会话产物
        ├── attempts/<attempt_id>/   每次执行的临时与输出目录
        └── .pi/                     transcript 与会话设置
```

以上是建议布局；现有代码的 transcript 与 cwd 尚在同一个 `session_dir/session_id` 目录，没有这些完整子目录。

每个活跃会话采用独立 Pi RPC 进程；Python 仍拥有 `user_id → session_id → run_id → attempt_id` 的授权关系。该进程 `cwd` 指向本会话目录，使用本会话 transcript，接收本 Run 的短期权限。用户其他会话可以复用容器和明确共享的缓存，不应复用同一个有状态 Pi 会话。Pi 官方 SDK 也把一个 `AgentSession` 定义为一个 conversation，`cwd` 决定工作区与资源发现。[会话与 cwd](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/sdk.md)

同一会话提交和自动唤醒应串行，避免同时修改 transcript；不同会话允许有限并发。共享用户容器意味着同一用户的 session 之间没有强操作系统隔离。若产品后来允许把一个会话授权给另一个用户，必须改变挂载/进程边界或使用独立执行沙箱，不能仅把 cwd 换目录。

## 4. 长期复用靠存储，休眠靠生命周期

| 状态/操作 | 保留什么 | 对 Agent 的要求 |
| --- | --- | --- |
| 容器运行 | 用户卷、Pi 进程和 bridge | 有活跃 Run/子进程则不回收；检查全机与用户容量。 |
| Docker pause | 文件和正在冻结的进程 | 冻结不等于释放内存，不推荐作为单机省内存方案。[官方行为](https://docs.docker.com/reference/cli/docker/container/pause/) |
| 停止容器 | 用户卷、容器文件层；进程结束 | transcript/checkpoint 先提交；不能保留待完成的本地工具调用在进程内。 |
| 再次启动/重建 | 同一用户卷重新挂载 | 用持久 Run、transcript 和未确认事件恢复，不依赖旧 PID。 |
| 删除容器、保留用户卷 | 文件持久化 | 换镜像时适用；恢复的运行环境由固定镜像和文件状态共同决定。 |
| 删除用户卷 | 用户工作区消失 | 仅是明确的数据删除动作，不能伴随普通空闲回收或发布。 |

CPU 沙箱等待远程 AF3 时，持久化 Run 为 waiting 后可停止 Pi 进程甚至停容器；A6000 服务继续计算，Python 收到任务完成事件后唤醒同一用户、同一会话。不存在为保一个等待 AF3 的 Pi 进程而必须把用户容器常驻的要求。已有 [agent._resume](/home/jhli/pskit-2.0/new_backend/app/services/agent.py:373)和扩展事件可作为现有对接点。

建议管理接口只有 `ensure_user_sandbox`、`start_session_attempt`、`cancel_attempt`、`renew_activity`、`stop_if_idle`、`replace_runtime_keep_volume` 与 `read_usage` 这些用途，不把 Docker 任意参数透传给用户。具体采用现有 bridge 或 OpenSandbox，可通过同一个后端 adapter 接口替换。

## 5. 接入 OpenSandbox 时的具体核对点

OpenSandbox 可减少自己维护通用文件/执行/生命周期 API 的工作，但其运行时默认值必须逐项检查。官方当前配置允许 `secure_runtime` 为 gVisor/Kata；默认空值仍是普通 OCI/runc，框架名字不证明强隔离。[运行时配置](https://github.com/opensandbox-group/OpenSandbox/blob/main/server/configuration.md)

建议的对接职责：

| PSKit 负责 | Sandbox provider 负责 |
| --- | --- |
| Supabase 用户、角色、项目/会话所有权 | 创建/连接/停止或重建执行环境 |
| Token/CPU/GPU 配额、预占、结算和审计 | CPU/内存/PIDs 运行时限制、执行进程与文件 API |
| Run 状态、自动唤醒、任务与工具授权 | 子进程日志、退出状态、取消与资源观测 |
| 哪些模型/Skill/文件可用 | 已核准的固定镜像与挂载 |
| 网关/模型服务凭据与身份代理 | 执行出网策略，不能任意直达数据库或模型管理 API |

对接前需核对所选固定版本：

1. **网络**：官方 Docker 配置的默认 `network_mode` 为 host；bridge 映射端口默认 `publish_host=0.0.0.0`。必须主动限制私网/loopback、鉴权与 sandbox endpoint，而不是复制示例后认为端口天然私有。
2. **出网**：请求包含 `networkPolicy` 才接入 egress sidecar；Docker 要用兼容的 bridge 模式。网络策略须覆盖 DNS、IP、代理、IPv6 与私网访问；不能只允许域名却把旁路漏掉。
3. **持久化**：将 sandbox ID 视为运行实例 ID，另存用户卷与 owner。对进程失效、TTL 到期和冷重建分别定义行为；Docker `resume` 不能假设可重启一个已停止/失败的 sandbox。
4. **数据库**：当前 server-managed metadata 支持 PostgreSQL；可规划独立 database/role 共用现有 PostgreSQL 实例，不能使用 Supabase 管理员角色，也不能凭这一点声称整个沙箱平台与 PSKit 已共享账本。
5. **版本**：镜像/SDK/API 与运行时成套固定，明确哪些能力是 stable、experimental；不为当前需求启用未验证的 credential vault、VM 内存快照或大型 Kubernetes 扩展。

依据：[完整服务器配置](https://github.com/opensandbox-group/OpenSandbox/blob/main/server/configuration.md)、[生命周期语义](https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/components/server.md)、[出网实现边界](https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/components/egress.md)。

## 6. 控制与计量要分层

### 进程、权限、网络与密钥

用户进程不挂 Docker socket、宿主目录、数据库网络或管理面凭据。镜像非 root、只读根、capability 清空、seccomp、PIDs、内存与 CPU 上限是基础。对公网任意代码，再增加 gVisor/VM 边界与出口代理。[Docker daemon 权限](https://docs.docker.com/engine/security/)、[Docker 资源限制](https://docs.docker.com/engine/containers/resource_constraints/)、[gVisor 外部网络/资源要求](https://gvisor.dev/docs/architecture_guide/security/)

上游模型 key 放在 LiteLLM/Python；沙箱仅获得 Run 范围权限。当前 [AgentService._environment](/home/jhli/pskit-2.0/new_backend/app/services/agent.py:184)已有 Run tool token 与 internal model proxy 的接点。现有 Pi 子进程会继承容器环境，见 [pi_rpc.py](/home/jhli/pskit-2.0/new_backend/app/adapters/live/pi_rpc.py:99)；开启 shell 后，不能再假设环境变量对子进程或同 UID 代码保密。bridge 的长期 token 应收紧持有范围、可撤销，Run token 应有操作范围、过期与执行状态校验。

同用户并发会话可属于同一信任边界；不同用户不共用 bridge token、volume 或不受控执行网络。新建沙箱只访问授权网关；文件 API 接口也要校验真实路径、symlink、文件大小和授权，不能仅由调用方传 `user_id` 证明所有权。

### CPU、内存和磁盘

Docker `--cpus` 限制的是可用 CPU 调度额度，不是用户每日累计 core-seconds。1 CPU 限制也不代表启动一个容器就立即占满一个核；活跃容量需按运行中的 workload 管理。内存上限与累计配额同样不同。[资源限制](https://docs.docker.com/engine/containers/resource_constraints/)

共享用户容器并发两条会话时，不能把容器 CPU 总增量记给其中任意一条。需要每个 attempt 的独立进程组/受控子 cgroup 或可信执行器归因，按所有子进程累计 CPU；若执行层暂不支持归因，就只公布可信的“用户沙箱总 CPU 使用量”，不要伪造每会话精确统计。

磁盘卷也需要容量、inode、上传和产物保留上限。当前命名卷没有自动硬容量限制；文件 API 中的业务限额不能阻止 shell 直接生成海量文件。选用有容量约束的文件系统/块设备/volume driver，或者由执行器严格限定可写目录与产物规模。磁盘容量、磁盘 IO 和存储保留天数分别配置。

### GPU 交给模型服务执行域

CPU 沙箱只调用 MCP/计算网关；GPU 调度、显存容量、并发、取消和计量由计算执行域负责。理由是同门模型服务通常需要自己的依赖、权重和 CUDA 版本，按模型长期驻留共享服务更自然；用户沙箱可独立快速启停。

这不是因为 gVisor 绝对不支持 GPU：官方已有 `nvproxy` 支持，但受 GPU、驱动、平台与配置兼容性约束。[GPU 支持文档](https://gvisor.dev/docs/user_guide/gpu/) 当前项目无需为 Pi 直接开放 GPU，模型服务的资源策略仍需要单独设计。

GPU 模型返回 `task_id` 后，Run 与 quota reservation 由 Python 保存；Task 完成后结果进入持久化事件，再唤醒 Pi。沙箱停止与模型服务停止是不同状态。这样用户关网页、沙箱被回收或 Pi 退出，都不会天然中断已由计算服务接收的作业。

## 7. 当前代码启用前必须补的对接项

以下是工作边界和未确认项，不是已执行清单：

- **活动生命周期**：active attempts/心跳/lease 与 idle sweep 联动；重启管理器后从持久状态恢复，不能从最近 ensure 时间推断空闲。
- **镜像替换**：现有 ensure 在固定名字容器镜像不同后返回 409，见 [sandbox_manager.py](/home/jhli/pskit-2.0/new_backend/app/sandbox_manager.py:215)。需要先 drain，再保卷重建；不能通过修改标签默默升级活跃用户容器。
- **双向文件**：uploads → 会话授权目录、产物 → backend Artifact；两边引用同一 file/artifact ID，校验版本、大小与所有权，不让模型拿宿主路径。
- **并发归因**：Run、attempt、进程与计量范围绑定；同一 session 串行，不同 session 并发受用户和全机容量控制。
- **出口网络**：沙箱与数据库、manager、其他用户 bridge 分隔，只能调用必要网关；容器的 app 内网不能视为完整策略。
- **凭据范围**：短期 Run token、最少允许操作、撤销与失效；真实 provider key 和 Supabase service role 不进入用户容器。
- **容量与故障**：磁盘/inode/日志上限、OOM/磁盘满/子进程泄漏/取消确认与剩余资源释放；退出码与错误形成持久 Run event。
- **回退路径**：sandbox transcript 位于沙箱卷，local Pi 无法直接读取。需数据 adapter 或暂时保留旧执行 provider，不能只移除 overlay 即宣称历史会话完整回退。[现有部署说明](/home/jhli/pskit-2.0/deploy/agent/SANDBOX.md:41)

本轮没有比较性能数字或宣称哪个方案已经在 ECS/A6000 兼容；没有读取云端密钥、创建测试容器、调用付费模型或变更现有 AF3 接收器。需要实际验证的主机条件包括 rootless 的 cgroup controller、KVM/嵌套虚拟化、存储 quota 支持、gVisor 的 Node/Python 兼容性，以及目标版本 SDK/API 行为。

## 8. 建议给同门的统一边界

同门接入的是 **模型服务/任务执行协议和可选 Python 包装器**，不是拿一个用户沙箱或一个 MCP 名称就自动获得 GPU 调度。

用户沙箱和服务容器可各自独立：`Pi → Python 工具与配额入口 → MCP/计算网关 → 同门模型服务`。同门只需暴露输入 schema、执行/任务状态、取消、产物和可信的 usage/attempt 关联。模型服务不获得跨用户 Supabase 权限；资源控制与记账不能完全依赖一个 decorator 自觉计时。

沙箱 provider 的 SDK 负责“在哪里执行”；MCP 负责“工具如何被发现和调用”；PSKit 的任务与配额服务负责“谁被允许用多久、异常后如何结算和再唤醒”。这一划分使用户可以选择现成 SDK，同时保留科研服务与每日用量的统一规则。
