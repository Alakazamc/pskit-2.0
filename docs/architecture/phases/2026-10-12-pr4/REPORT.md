# PR-4：发布控制器内部拆分与当前部署索引

## 基线与修改文件

实施基线 `eb8df70`，承接 PR-0/1 的远端六项 CI green；整合提交的精确父版本与 Actions 结果在最终索引记录。只在原目录增加私有职责：`deploy/agent/scripts/release_config.py`、`release_api.py`、`release_web.py`；原 `release_control.py` 保留 Controller/CLI facade、公开导入名称及活动观察。新增 `deploy/agent/tests/test_release_control_modules.py`，更新 `deploy/agent/README.md`、`docs/architecture/DEPLOY_MATRIX.md`、`COMPLETION_REPORT.md`。

原控制器集中处理纯配置、安全验证、应用切换及恢复，难以单独审查。先移动 fingerprint/normalization 纯函数，再移配置操作，最后分别移 API 与 Web 操作。API enter/verify/release、owner/nonce、fencing、commit、失败闭锁和恢复保留在同一个 `_ApiOperations`；没有将准入状态机切碎。原有 release_files/manifest 继续复用，无框架或额外服务。

保留本轮开始前工作区独立的 compute_outbox SQL 修改；它不进入本阶段机械拆分提交。源文件和 `_activity` 的初始字节证明保存在 `/tmp/pskit-remediation-20261012/pr4/`；用于提交的 clean patch 单独从初始 HEAD 构建。

## RED / GREEN / 等价证据

| 边界 | RED | GREEN |
| --- | --- | --- |
| 配置纯函数 | 新 seam 缺失，2 failed | 96 passed（含原94项 controller） |
| 配置/Compose 注册 | seam 缺失，1 failed | 97 passed |
| API owner/失败/回执恢复 | seam 缺失，3 failed | 121 passed（含 receiver） |
| Web 原子切换/中断/恢复 | seam 缺失，3 failed | 146 passed（含 Manifest） |

最终相同146项分别在当前工作区与只应用 PR-4 的干净基线副本通过；未删除原94项 controller 测试。Ruff/compileall/diff-check通过。[机械等价结果](mechanical-parity.json) 对31个原方法展开委托后比较 AST，均相同；原顶层函数/常量、模块名字和活动观察字节一致。工作区初始1262行控制器变为711行 facade，三个模块为275/328/76行；这些数字包括原有未提交 SQL，不能作为业务行为改变的证据。

隔离干净候选完整 Deployment **458 passed / 0 skipped / 28.49s**，同一 runner 验证 PG17；合成 Compose **9/9** 与31方法机械比较通过。候选包含最终 MCP 文件，未包含原先 compute_outbox SQL。完整日志与最终冻结哈希：`/tmp/pskit-remediation-20261012/pr4/integrated-f16a09c/`。最新 API 候选镜像3项和316个运行源文件逐一哈希一致；最终阶段源与远端CI结果在最终索引补齐。线上 source bind、当前宿主 adoption、真实 Staging Web/API/中断/长任务/回滚及 RPO/RTO **未在本轮执行**。不将合成测试当成上线资格。

## 文档与不变量

部署矩阵列出17份 agent YAML 的环境、机器、项目、叠加顺序、私有 env 用途、source bind、卷、网络和恢复入口。没有删除/改名 YAML，未合并 Supabase/LiteLLM/Agent 项目，bootstrap/migration 独立于日常 app release。README 提供唯一当前入口索引，历史联调资料有明确时间边界。整合报告顶部说明 `apps/*` 已完成纯目录迁移；保留过去“当时未迁移”的证据。

API 日常发布继续要求本宿主合格 durable barrier 和零活动/unknown/待结算证明。Web 继续 assets 先发布、index 后原子切换、保留旧 hashed assets。缺 Manifest/宿主资格/有效回执继续 fail closed，不重新初始化数据库、不 down 全栈、不删卷。

## 回滚

revert 本阶段提交，三私有模块的方法回到原 facade；`pskitctl` 参数、Manifest 格式、数据库 schema、卷、Web artifact 格式均无迁移。若后来获得授权并实际执行发布，仍按注册 Manifest 的 app-only 恢复入口操作，先核对 pending/admission owner 和 schema；不能把代码 revert 当成数据库降级。本轮未部署，生产状态不变。
