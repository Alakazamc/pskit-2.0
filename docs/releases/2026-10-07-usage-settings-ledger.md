# 用量明细诊断与设置页面发布

日期：2026-10-07。代码提交：`9e5aaacc3c99d56c793d3b3834333c0e24994164`。

## 诊断

用户截图显示同一轮对话的正负 Token 流水。通过生产 PostgreSQL 只读事务，核对持久账本、实际模型用量和 `activity_for` 的统计结果；没有提交新对话、模型请求或 GPU 任务。

| 流水 | Token | 含义 |
| --- | ---: | --- |
| reservation | +1 | 对话准入预扣 |
| adjustment | +21,135 | 正文模型请求前追加预扣 |
| model_attempt / completed | +1,381 | 正文实际用量的审计记录 |
| adjustment | −19,755 | 退回未使用的正文预算 |
| reservation | +3,190 | 自动生成标题的预算预扣 |
| model_attempt / completed | +506 | 标题实际用量的审计记录 |
| adjustment | −2,684 | 退回未使用的标题预算 |

账务净额为 `1 + 21,135 − 19,755 + 3,190 − 2,684 = 1,887`；实际用量为 `1,381 + 506 = 1,887`。`model_attempt` 记录调用事实，不再次增加账户扣款。这轮没有重复扣费。问题是设置页把内部预扣、调整和实际用量都称为 Monthly Token quota，并展示成相同的用量条目。

方块图已按实际模型调用统计：排除 reservation 和关联 Run 的 adjustment。生产当日实测合计 2,066 Token。月度账户已用为 77,979，实测调用为 14,271，差额 63,708 对应三次未取得提供商用量的保守预算留存；不能把这个差额当作已确认的模型消耗，也不能在没有对账证据时直接退款。本次保留既有结算策略。

## 修改

- 删除设置页 Recent usage 明细、排序与状态映射，以及对应 CSS。
- 删除设置页对 `/usage/entries` 的请求；保留后台账本和接口供审计使用。
- 保留额度摘要和 Token / GPU 方块图，以及日期详情、键盘导航和手机横向滚动。
- 新增中英文页面回归，确认方块图仍显示且不再请求内部流水；更新已有设置页测试。

## 验证

- 新增回归先失败两项，再完成修复。
- 前端：`VITE_TURNSTILE_SITE_KEY='' npm test`，42 个文件、265 项通过；验证码专用测试仍显式设置站点 key。
- 前端 typecheck、lint、生产构建通过；构建使用现有生产公共 Turnstile key，没有使用本地测试 key。
- 后端用量活动、流水和 PostgreSQL 配额回归：11 项通过，使用本地独立测试 schema。
- 浏览器检查中英文 × 深浅色 × 桌面 1440 px / 手机 390 px，共八组；365 个日历格、七行、指标切换、箭头键导航正常，无页面横向溢出，无 `/usage/entries` 请求。
- Staging：相同静态制品五个入口资源核对通过；登录、文件、SSE、额度、模拟 AF3 smoke 通过，生产数据库快照未变。
- 生产：登录及四个入口资源的响应与构建文件逐字节相同；未登录用量 401、internal 404、公网管理入口 403。浏览器加载生产静态资源并使用接口替身，确认方块图显示且没有明细和流水请求；该检查没有创建生产会话。

## 发布

仅发布 React 静态文件。后端镜像、Pi 模式、Nginx 配置、数据库和私有配置保持现状，无容器重建。生产与 Staging 后端均健康，就绪接口返回 live identity / Pi。

- 服务器制品：`/home/ecs-user/pskit-agent-releases/20261007-usage-calendar-9e5aaac`。
- 归档 SHA256：`3aeaceb453ad4015e00442b5a2e1e13b2dda2a807032b575d59213b8a29051a2`。
- 完整 dist SHA256：`cf06746efcce8ab0193168dfdba26c1a96160325c7c914deab17e3acd415a45d`。
- index SHA256：`405b31f0a2c7e1fe80abe11ca2a9a4f2cd10da47dcc8a923c15756f381b21e02`。
- 后端镜像 ID：`sha256:d3a04483ccb92ae8b477e57e5e850061bcf598d355e3c6e2533e5c9614fd343e`。
- 发布脚本核对旧源码哈希，备份完整静态目录，先复制 assets，再原子替换 index；保留旧 assets。

## 恢复旧页面

只有本次页面发布需要撤回时，才在阿里云执行下面命令。它恢复旧 index，旧 assets 已保留，不操作数据库或后端。

```bash
python3 - <<'PY'
import os
import shutil
from pathlib import Path

root = Path('/var/www/agent.bioailab.net')
backup = Path('/home/ecs-user/pskit-agent-releases/20261007-usage-calendar-9e5aaac/rollback/production-static/index.html')
temporary = root / '.index-usage-rollback'
shutil.copyfile(backup, temporary)
temporary.chmod(0o644)
os.replace(temporary, root / 'index.html')
PY
```

旧公开源码位于本次 release 的 `rollback/public-source/`；Staging 的旧静态目录和 manifest 同样留在 `rollback/`。恢复后重新核对页面与入口权限。
