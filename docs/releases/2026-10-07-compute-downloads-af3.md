# CORAL 下载与 AF3 Tools 发布记录

日期：2026-10-07。发布目录：阿里云 `/home/ecs-user/pskit-agent-releases/20261007-downloads-af3-ed9da4f`。

## 用户入口

- CORAL：`https://agent.bioailab.net/tools/coral`。四种能力保留，结果文件通过拥有者下载 API 返回实际内容。
- AF3：`https://agent.bioailab.net/tools/af3`。填写 AlphaFold 3 JSON，提交真实 A6000 任务，查看状态、结果文件及该工具的历史。需要正式登录和足够 GPU 额度。
- AF3 过去已有计算容器和兼容 API，但未注册为已审核的 Tools 产品。此次补齐注册、验收、UUID spool 兼容和持久文件传输。

文件在后端校验大小与 SHA256 后存入现有 PostgreSQL 表。结果报告确认后才允许拥有者下载；文件或结果 ACK 丢失只重试传输，不重跑模型。数据库 schema 保持 10。

## 已发布制品

| 项目 | 事实 |
| --- | --- |
| 后端 / 前端应用源码提交 | `ed9da4fa8a0619b4318a17011137d12b7ff103b4` |
| 后端镜像 | `pskit-agent-backend:20261007-downloads-af3-tools` |
| 镜像 ID | `sha256:d3a04483ccb92ae8b477e57e5e850061bcf598d355e3c6e2533e5c9614fd343e` |
| 原始源码归档 SHA256 | `44ac5bf60fad4e015c0817c55b30f4e080faca3782910c81722775ed46c11dba` |
| 最终前端归档 SHA256 | `c1c92db73b482d14a1726426b1c931f6ebd9eb04ffc0627ee7f7637ce6a61cd6` |
| 最终前端树 SHA256 | `27f953d5ccf7243aca4e5ae827c4181885a539dd7a99727b31982c260086be76` |
| 最终 index SHA256 | `35aea58e20cdb60f472ad5bb64a4044f3a4ce94b0431312f8f7202437cb87f58` |
| 提供端启动修复 | `d46531f`、`3e9e096`；保留原模型模块路径、检查可执行文件、使用绝对 workspace |
| CORAL 产品配置修复 | `6ede868`；CPU 分析仍要求 CPU/wall 数值，允许其原生报告保留未知 GPU 来源 |

后端、A6000 接收器的应用运行源码保持固定快照。后续提供端启动脚本和数据库产品配置修复单独留存版本，无需重建模型镜像。生产前端继续由宿主 Nginx 提供；数据库、邮箱与 LiteLLM 私有配置保留。

## 真实验收

| 检查 | 结果 |
| --- | --- |
| 相关后端回归 | 221 passed，48.67 秒 |
| 前端组件回归 | 13 passed；typecheck、lint、build 通过 |
| CORAL 一次生成、迭代、口袋、二维分析 | 4 个真实案例通过；二维案例跳过结构计算 |
| AF3 原生预测验收 | 10 个氨基酸、1 seed、无 MSA/template，真实结果通过 |
| 公网用户 CORAL Run | `tool-run-d096b0dc-246b-48ad-98d9-1c415ba9fa32` 完成，1 个 CSV 下载并校验 |
| 公网用户 AF3 Run | `tool-run-0cc46390-718a-436b-8235-676433396694` 完成，19 个 CIF/JSON 下载并校验 |
| 历史文件恢复 | 2 个旧 CORAL Run、2 个文件；拥有者经 HTTPS 下载验证，无重新推理 |
| 接收器回执 | CORAL、兼容 AF3、Tools AF3 三份 journal 的未确认记录均为 0 |
| 原生 AF3 容器 | 未重启，StartedAt 仍为 `2026-10-02T05:50:17.312602338Z` |
| 管理入口隔离 | 公网 UI/API 403；WireGuard UI 200、未认证 API 401 |
| 内部接口 | 公网 404；无 Bearer 的私网 AF3 MCP 返回隐藏端点的 404 |

CORAL 发布 revision 5，服务 revision 6，report `qualification-b0dbefd4-2b85-4b3d-ae7d-65bac3800106`。AF3 发布 revision 1，服务 revision 1，report `qualification-34f0da64-e498-443d-aebc-1380e92ed5c9`。验收没有测试或宣称真实取消成功。

此次真实用户验收临时增加了受控测试账号的额度，结束后恢复原值，实际消耗和文件仍保留。AF3 的 GPU 时间来自旧计算器取整分钟，标记为 `estimated`；CPU/wall 无数据时保持空值。

## 发布过程中修正的问题

最初提供端重启脚本覆盖了原有 `PYTHONPATH` 并使用了未激活的 PATH，造成模块和 Foldseek 缺失。现已恢复原有模块路径、显式配置 Python/Foldseek，并固定绝对 workspace；四种能力重新验收通过。

最终配置核对发现第一次静态构建沿用了本地 Turnstile 测试站点 key。先恢复旧登录 index，再按云端现有公共站点 key 重建静态文件；同一份修正制品在 Staging、生产分别验证入口与四个初始资源。最终运行记录确认站点 key 哈希匹配且存在于当前 JS assets 中。后端秘密 key 未修改。

相关回归全部通过；全量历史后端测试中的旧迁移与认证测试夹具有既有失败，此处不宣称全量绿色。小型 AF3 验收也不代表大蛋白、多 seed、MSA 流程或所有 GPU 负载已做性能验收。

## 证据与恢复

云端发布目录保存 `release.json`、`production-release.json`、`staging-verified.json`、`corrected-frontend-release.json`、`final-runtime-checks.json`、`historical-download-checks.json`、`coral-unified-user-smoke.json`、`backfill-coral.json`。A6000 `/data/jhli/pskit-mcp-receiver-20261007/downloads-final-receiver-checks.json` 保存 journal 和原生容器核对结果。

旧镜像、原私有配置与完整前端静态目录保留于发布目录 `rollback/`；A6000 原接收器配置保留于 `rollback-downloads-ed9da4f/`。数据库没有迁移，不应覆盖发布后新增会话或任务。回退产品版本时另核对发布记录和活动 Job；不要清空尚未确认的 journal 或 spool。
