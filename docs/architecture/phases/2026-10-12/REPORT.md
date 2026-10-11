# 2026-10-12 最新整改交付索引

## 范围与基线

按[用户附件归档](../../REMEDIATION_EXECUTION_2026-10-12.md)，在 `codex/new-stack-baseline` 的原 HEAD `c37ca61e7a8d9672013597f2cc339f4e9060720f` 上执行。先核对 [Actions 38149526876](https://github.com/Alakazamc/pskit-2.0/actions/runs/38149526876) 实际日志；其 Backend 没有运行 pytest、Frontend 为8失败、依赖镜像 job 被跳过。PR-0/1精确HEAD远端六项全绿后才进行后续阶段。

每次门禁使用干净版本加本轮指定文件；不纳入开始前工作区的流式排序、准入、生产发布资料及配置改动。未删除失败用例、新增skip、增加原用例超时、关闭业务功能或再次迁移目录。原生 subagent 分工已经说明；本宿主没有完整 All in Luna Coordinator 接口，未伪造 Lane/Run。

## 独立提交与阶段结果

| 阶段 | 独立提交 | 根因 / 最小修复 | 文件、RED/GREEN与回滚 | 精确HEAD远端门禁 |
| --- | --- | --- | --- | --- |
| PR-0 | `f088128` | import block 多余空行导致 Ruff I001；仅修格式 | [PR-0/1报告](../2026-10-12-pr01/REPORT.md) | 包含在下一行精确HEAD门禁 |
| PR-1 | `eb8df70` | Query取消撤销乐观确认；7个图片fixture跨realm Blob不符合Node22 Response；独立乐观层与稳定可选ID、原生正确MIME替身 | [全部8问题、修改文件与回滚](../2026-10-12-pr01/REPORT.md) | [38156874714：六项成功](https://github.com/Alakazamc/pskit-2.0/actions/runs/38156874714) |
| PR-2 | `f16a09c` | raw结果摘要泄漏；仅固定生命周期、错误码白名单和整数exit code | [115项安全RED/GREEN与回滚](../2026-10-12-pr2/REPORT.md) | 包含在下一行精确HEAD门禁 |
| 独立正确性补丁 | `9da18a1` | 通用线程池饱和阻塞MCP续租、重复取消打断清理；独立续租worker和受保护的cleanup | [确定性复现与回滚](../2026-10-12-mcp-capacity/REPORT.md) | [38160933404：六项成功](https://github.com/Alakazamc/pskit-2.0/actions/runs/38160933404) |
| PR-3 | `25673ec` | 假阳性资格、分片/terminal、alias未调用、普通/资格wire不一致、extra_body绕过；可信opt-in共享策略，固定SDK离线验收 | [完整模型修改/验收与回滚边界](../2026-10-12-pr3/REPORT.md) | [38162074647：六项成功](https://github.com/Alakazamc/pskit-2.0/actions/runs/38162074647) |
| PR-4 | `ba9e214` | release controller职责集中；同目录私有Config/API/Web模块，原状态机/CLI/manifest保持 | [31方法等价、458门禁及17YAML矩阵](../2026-10-12-pr4/REPORT.md) | 包含在下一行精确HEAD门禁 |
| PR-3兼容跟进 | `463b2ef` | 新Case诊断字段令旧完整资格JSON读失败；诊断marker留内部，Case原5字段，False策略不写新字段 | [5 RED→6 GREEN、229 focused及固定2.14完整旧schema](../2026-10-12-pr3/REPORT.md) | [38163205284：六项成功](https://github.com/Alakazamc/pskit-2.0/actions/runs/38163205284) |
| PR-5 | 本报告所在提交 | 新Run默认使用已绑定业务Tool；server-only兼容开关固化JSON、旧Run缺字段仍恢复通用调用 | [修改文件、账本/唤醒/幂等与回滚](../2026-10-12-pr5/REPORT.md) | 本报告所在HEAD触发最终[新栈CI](https://github.com/Alakazamc/pskit-2.0/actions/workflows/new-stack.yml)；交付答复提供精确run链接 |

CI结果只属于链接中实际 `head_sha`，不是为所有祖先虚构独立成功运行。PR-2/MCP和PR-4/模型兼容跟进分别在整合HEAD验证。旧失败及中间失败日志保留。每个提交可单独审查；本任务没有创建GitHub PR。

## Actions 的8个前端问题逐项闭环

| 原失败用例 | 本地RED证据 | 最小修复 / GREEN |
| --- | --- | --- |
| `does not consume accepted optimistic messages when a latest-page refresh is cancelled` | 原14项独立执行在Node22/24均通过；固定“API响应已返回但Query尚未commit”取消边界，新回归1 failed/14 passed，另有游标/精确ID边界RED | 乐观显示与Query取消解耦、成功commit才确认；历史27/27 |
| `rebuilds a persisted image thumbnail from the owned file API after a refresh` | Node22锁定fixture RED | 原生PNG Response；previews4/4 |
| `does not bring an attachment back when its thumbnail arrives after removal` | Node22锁定fixture RED | 保留晚到/删除断言；previews4/4 |
| `loads saved same-name images above a separate user text bubble through owned file IDs` | Node22锁定fixture RED | 保留文件ID归属；messages4/4 |
| `shows pending image previews while retaining the message text` | Node22锁定fixture RED | 保留pending正文/预览断言；messages4/4 |
| `keeps a named retryable fallback when an owned image cannot be downloaded` | Node22锁定fixture RED | 保留失败重试；messages4/4 |
| `isolates late image downloads when switching conversations` | Node22锁定fixture RED | 保留跨会话晚到隔离；messages4/4 |
| `uploads avatars without exposing credentials and removes them through the same owner API` | Node22锁定fixture RED | 原生WEBP Response；settings6/6 |

7个图片问题在Node22/npm ci原样复现 **7 failed/7 passed**。4文件共41项连续3轮通过，随后完整Frontend400项通过。原历史用例受执行时序影响，并未伪称本地原样稳定失败；新确定性边界用例在修复前RED，在修复后GREEN。详细日志分别为 `/tmp/pskit-remediation-20261012/images/red-node22-locked-*.log` 与 `history/red-original*.log`、`history/red-boundary.log`、`history/red-locked.log`。没有仅放宽 waitFor 或绕过Blob解码。

## 最终本地门禁

| 门禁 | 最终结果 | 完整证据 |
| --- | --- | --- |
| Backend Ruff/compile/full | **1516 passed / 1原有外部skip / 320.44s**，JUnit零failure/error；其中 **PG234 passed / 0 skipped** | `final-gates-backend/backend.{log,xml}` |
| Frontend | **400 passed / 53 files**；typecheck、lint、build/Molstar通过 | `final-gates-ui/`；6命令累计93.748s |
| Pi | **11/11内部场景**；真实固定Pi RPC也在Backend门禁内 | `final-gates-ui/pi.log`、Backend JUnit |
| Contracts | 导出再生一致；**121 paths / 179 schemas**，仅RunRef/ModelWirePolicy可选属性变化 | `pr3-rollback-review/green-generated-contracts.log`、`final-contract-parity.json` |
| Gateway Protocol | 固定LiteLLM1.100.3原生兼容5项、严格wire4项；断网真实SDK传输替身 | `pr3/green-final-native-compat.log`、`green-final-native-wire.log`；上述精确远端gateway job再次通过 |
| Deployment | **458 passed / 0 skipped / 28.70s** | `final-gates-deployment/deployment.log` |
| 合成Compose | **9/9** | `final-gates-ui/compose.log` |
| 本地Image | **3 passed / 0 skipped**，316运行源逐一匹配 | `final-image/` |

原始证据根为 `/tmp/pskit-remediation-20261012/`；当前stage摘要见[机器可读结果](verification.json)。测试环境为隔离source快照、Node22.23.3/npm ci锁、Pi0.87.1、专用loopback/tmpfs PostgreSQL17.6随机schema，凭据环境清洗；正式数据/私有env没有进入测试。

最终本地候选镜像 `pskit-agent-backend:remediation-20261012-final`，ID `sha256:0bf9cb9388bc1f66d44e7943d9ab725cbbf857a6dd1953ca18f9f859c289b471`；镜像检查 **3通过/0跳过/6.16s**，运行源 **316文件匹配/0缺失/0额外/0不一致**。官方基础digest与npm/Python锁不变，固定SDK/非root检查通过；只保留本地，没有推送镜像或部署。完整build/source/parity证据在 `/tmp/pskit-remediation-20261012/final-image/`。

**源码对应边界：** 当前Git index所含业务代码、测试、CI与候选SCOPES **1061文件全部一致**。Backend/UI起始快照的部署README仍为旧文档，之后已刷新；两者所有代码/测试/CI字节一致，Backend测试无README引用。UI各实际命令均exit0，但额外末尾全manifest稳定断言检测到这一个文档刷新而exit1，保留在报告中，不把它伪写为整体wrapper成功。部署门禁使用刷新后的README；远端最终精确HEAD仍须全门禁。唯一差异的hash/复核在 `final-gates-ui/results.json`。

本地venv为Pydantic2.13.5；镜像和GitHub锁为2.14.0，最终完整旧JSON的2.14.0证明另行通过。不把本地环境版本差异隐藏为锁定CI。

## 契约与回滚

公开path/SSE/Compute及既有required字段保留。PR-1仅增加稳定消息关联的可选 `RunRef.user_message_id`；PR-3仅增加可信profile可选wire opt-in，False序列化保持旧格式/指纹。Case不新增字段。默认新资格、旧资格、历史Run兼容的完整旧schema已冻结验收；显式True策略不能回退到不理解该策略的任意旧binary，需要支持它的回退制品。无真实profile被本任务改动。

按各阶段独立提交revert代码，不降级数据库、不删卷、历史、journal/outbox或workspace。PR-2 revert会恢复安全暴露，应仅用于隔离比较或被修复替代。PR-4保留Manifest app-only恢复与durable owner/fence/闭锁规则；代码revert不是生产数据库回滚。PR-5旧程序可忽略新增context JSON策略，旧通用Tool端点和异步唤醒仍在，不要删除历史恢复接口。

## 尚需另外授权的外部验收

- 新真实供应商在独立Staging完成 genuine tool calling → result continuation → workspace write/read → artifact.created →鉴权下载与usage结算。离线证据不会授予真实模型Agent Ready。
- 当前宿主adoption/source binds、真实API/Web/中断/长任务回滚和RPO/RTO演练。合成部署门禁不等同实机发布资格。
- 历史通用Compute的真实使用量审计、长期观察与最终删除决定。当前仅完成兼容收敛，不据单元测试删除旧能力。
- 原有外部Supabase Auth/Storage集成测试需独立环境；没有新增skip，也没有将该未执行测试标为通过。

本轮未SSH、未部署生产或云端Staging、未更改真实数据库/密钥/付费模型配置、未删除历史数据；当前生产版本没有被重新在线核实。按用户本次明确约束，交付仅限当前分支代码、验收与远端CI。
