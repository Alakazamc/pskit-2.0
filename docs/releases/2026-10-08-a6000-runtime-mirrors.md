# A6000 接收器与依赖镜像源收尾

日期：2026-10-08。主仓库源码：`e164dab8363915cd2e8daff5bc8fbf6169712af0`。

## 内容

- 后端 Dockerfile 保留固定 Node/Python 版本和 digest，同时允许通过显式构建参数选择公开 HTTPS PyPI、npm 镜像和已核对的基础镜像引用。
- A6000 唯一通用 MCP/AF3 接收器从 `runtime-c1d7407` 切换到不可变 `runtime-e164dab`；AF3 模型计算容器未替换、未重启。
- 4090 CORAL MCP 的一次生成失败路径增加同进程 CUDA 计时，输入校验和执行前取消记录为实测零 GPU；迭代和远程失败仍保持未知用量。产物读取协议与该修复一并固化在 CORAL 仓库提交 `25afdc0921a70d5c1c505c68148b4fa78ccd0df0`。

## 制品身份

| 项目 | 值 |
| --- | --- |
| A6000 runtime | `/data/jhli/pskit-mcp-receiver-20261007/runtime-e164dab` |
| runtime 归档 | `/data/jhli/pskit-mcp-receiver-20261007/runtime-e164dab.tar.gz` |
| runtime 归档 SHA256 | `ce891c4eb800cf23ae32f94987196d9fad3f1622b7c0b438809d782332881d62` |
| 接收器固定镜像 ID | `sha256:67d5aa962921e08be408ebce0852bca6e86afaabbb39291634dcbb5bb59dcece` |
| A6000 发布清单 | `/data/jhli/pskit-mcp-receiver-20261007/release-e164dab.json` |
| 发布清单 SHA256 | `6acff219c2a5bcfafdb2e26a89d8672282d55ad882d7ff5b0594529fb4bfca21` |
| CORAL provider 提交 | `25afdc0921a70d5c1c505c68148b4fa78ccd0df0` |
| CORAL `server.py` SHA256 | `26f3186df9f29dd2cf7bf8c371561d1481726db4db25a861b405b5a45b7654a3` |
| 镜像源检查镜像 | `pskit-agent-backend:mirror-source-check`，本地 ID `sha256:d3b4f133cca6428f63f57e01bac98e64d488c6fd233f1453ce75297365b43c15` |

镜像源检查使用相同 digest 的本地基础镜像缓存、清华 PyPI 与 npmmirror npm。npm 安装层约 7 秒，Python 依赖层约 20 秒；与生产镜像的 `pip freeze` 无差异，Python、Node 22.21.1 和 Pi 0.87.1 导入检查通过。该检查镜像没有替换阿里云生产后端；它验证的是后续构建入口。

## 旧任务对账

切换前先处理两条 CORAL 记录，没有直接丢弃 journal。

1. `compute-700e7c142f0747268eb8cf40d9960696`：提供端日志证明领取后没有进入模型执行，管理员审计结算为 `cancelled`，wall/CPU/GPU 均为实测 0。
2. `compute-bc69f44441dc4427b874c121f4d34c4d`：新接收器发现提供端失败报告缺少必需 GPU 用量。按接收器至失败的 1.429 秒窗口保守取整为 wall、CPU 核和单 GPU 各 1,500 ms，管理员审计结算为 `failed`。随后修复提供端失败路径。

两条记录均为终态、`settled`，资源租约已释放，并存在 `usage:reconcile` 审计。云端不再有非终态或待对账 Job 后，才备份并删除 A6000 对应的已确认本地行。

## 验证

- A6000 接收器运行并挂载 `runtime-e164dab`；CORAL、兼容 AF3、Tools AF3 三个 journal 的未确认行均为 0。
- 原 AF3 计算容器 ID 仍为 `d9fbde903788f7bef6a14b99e6fd047d382df2a66b3bea7731f4b6b18a02a231`，启动时间仍为 `2026-10-02T05:50:17.312602338Z`。
- A6000 到 4090 的 MCP `tools/list` 返回四个已发布 CORAL 工具；4090 进程监听 8100，运行提交为 `25afdc0`。
- 失败计量使用不进入真实模型的合成 driver 验证；输入校验的零 GPU 路径也通过。
- 修复后没有额外运行真实 CORAL 或 AF3 推理，因此本记录不声称真实模型结果已重新验收。

## 回滚

程序回滚材料位于 `/data/jhli/pskit-mcp-receiver-20261007/rollback-e164dab`，其中保存切换前的 Compose 配置和两阶段 journal 副本。需要恢复时先再次检查中央 Job、当前 journal 和 AF3 spool，再将 `AGENT_MCP_RECEIVER_RUNTIME` 恢复为 `runtime-c1d7407` 并只重建统一接收器。不得覆盖当前 journal，也不得停止 AF3 计算容器。

CORAL provider 回滚只针对其独立仓库提交；回滚前必须确认没有模型调用执行中，并保留当前产物索引。阿里云生产 backend/frontend 本次没有切换，无需回滚。
