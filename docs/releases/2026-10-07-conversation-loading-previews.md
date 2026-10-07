# 会话加载与图片预览云端发布

日期：2026-10-07。发布源码：`f2c497fd4453be0f92be87784a0250003370afd9`。生产前一版前端为 `9e5aaac`。

## 本次内容

- `11b62c8`：Tools 目录等待两个目录请求结束后统一展示；复用单一加载组件；工具详情保留正式标题，冷加载不再先显示 slug。
- `15b8f87`：历史会话等待消息和 Markdown 渲染器准备完成后展示；缓存历史直接渲染；完成的 Run 不重播 SSE；切换会话重置滚动位置并恢复自己的草稿。
- `f2c497f`：图片预览按用户、文件 ID 缓存 Blob；切换会话恢复，刷新通过带鉴权的文件下载恢复；删除和卸载释放 object URL。同名文件按不同 ID 区分，不把图片内容或临时 URL 写进 localStorage。

只更新 React 静态文件及对应公开前端源码。生产、Staging 后端的容器 ID、镜像 ID、启动时间保持原值；没有迁移数据库、更新计算接收器或 reload Nginx。前端使用正式 Supabase 模式、`/api/v1` 和既有生产 Turnstile 公共站点 key，未使用本地 Cloudflare 测试 key。

## 制品

| 项目 | 值 |
| --- | --- |
| 阿里云发布目录 | `/home/ecs-user/pskit-agent-releases/20261007-navigation-previews-f2c497f` |
| 归档 SHA256 | `6064cf2c45fdc7fa260b7f18aad54568245d2702d3730381becd1c4f6103b5c6` |
| 完整 dist SHA256 | `1688bea2e485ef7b5f15d6e858f0d83ab9aa714cab549acd7fa37b7a14c81c30` |
| index SHA256 | `bce2ffac00cac25263013ddc5a0e5359e268f37c763a9a97d97a21fb0b60245f` |
| 保留的后端镜像 ID | `sha256:d3a04483ccb92ae8b477e57e5e850061bcf598d355e3c6e2533e5c9614fd343e` |

先发布同一制品至 Staging，再发布生产。分别备份完整旧静态目录，先复制新 assets，最后原子替换 index，保留旧 hash assets。21 个待同步源码文件均先与前一版本内容哈希核对。Staging manifest 只更新前端路径和哈希，运行中的后端镜像 pin 保持原值。

## 验证与边界

- 实现阶段已有 280 项前端测试通过，并通过 typecheck、lint；本次正式环境参数重新构建成功，Molstar 检查通过且未进入首屏依赖。
- 同一构建的本地浏览器检查：加载/历史流程八组，附件流程八组，覆盖中英文、明暗主题、桌面和手机。HTTP SSE 夹具的第一段文本在终态发送前显示，停止按钮保留至完成。
- 阿里云 Staging 实际接口烟测：合成账号登录、文件上传、SSE、配额和模拟 AF3 通过，四个事件、`simulation=true`。烟测前后宿主机只读 PostgreSQL 生产快照一致。
- 测试容器不挂载 Docker socket。生产快照在宿主机通过固定只读查询取得，容器只执行 Staging 的应用接口探针。
- Staging 静态页面浏览器检查：加载/历史八组，附件八组。接口使用受控夹具以验证延迟、相同文件名和失败边界；静态资源来自真实 Staging Nginx。图片检查经本机 localhost 转发，因为私网 HTTP IP 地址不是安全上下文，浏览器不提供现有上传流程使用的 `crypto.randomUUID`。这个限制仍存在于直接访问私网 HTTP 的上传流程；生产 HTTPS 不受此限制。
- 生产 HTTPS 浏览器检查：英文浅色桌面、中文深色手机各验证加载/历史和附件流程。页面和资源来自真实生产 Nginx，业务接口使用夹具；没有创建生产会话、发送 LLM 请求或提交 GPU 任务。
- 两环境制品文件均逐一比对；公网 index 和入口资源与制品逐字节相同。生产/Staging ready 正常，登录页 200、未登录 usage 401、internal 404、公网管理入口 403。

私有服务器发布目录保存 `staging-static.json`、`production-static.json` 和 `containers-preserved.json`。WSL 的对应浏览器与烟测摘要保留在 `/tmp/pskit-stage-attachment-preview-visual.json`、`/tmp/pskit-stage-route-loading-visual.json`、`/tmp/pskit-production-attachment-preview-visual.json`、`/tmp/pskit-production-route-loading-visual.json`、`/tmp/pskit-frontend-navigation-smoke.log`。

## 当前 CORAL 任务的只读观察

本轮还检查了用户的 `6vxx / A / num_samples=1 / length=50` 请求，没有重启、取消或重复提交该任务。

- Tool Run：`tool-run-0ba3b098-9eca-4b7a-bc65-1c3aa2c9fbbb`。
- Compute Job：`compute-700e7c142f0747268eb8cf40d9960696`。
- 后端于 `2026-10-07 04:54:19 UTC` 记录 A6000 领取，只有 queued、run.started、stage.started 三个事件。
- 检查时 Compute Job 为 `cancelling`，progress 和 latest_seq 均为 0；report 为空，GPU 账务为 `pending_reconciliation`。执行期限是 `05:04:19 UTC`。
- A6000 journal 保留为 `executing`，没有 pending 或 outbox 结果。接收器记录 `requires reconciliation (ProtocolError)`，随后 `execution unknown`；容器没有重启或 OOM。

这不能作为成功验收，也不能把页面 Running 0% 当成仍在正常推理。当前日志只记录协议异常类型，尚不足以确定原始模型错误。本次静态发布不修复该计算协议问题，也没有调整该任务的预留或结算；后续需保留原 journal，核对提供方的失败返回与计量契约，再处理终态回传。

## 撤回本次前端发布

如需撤回，在阿里云恢复本次备份的 index；旧 assets 已保留。以下命令只恢复页面入口，不操作数据库和后端：

```bash
python3 - <<'PY'
import os
import shutil
from pathlib import Path

root = Path('/var/www/agent.bioailab.net')
backup = Path('/home/ecs-user/pskit-agent-releases/20261007-navigation-previews-f2c497f/rollback/production-static/index.html')
temporary = root / '.index-navigation-rollback'
shutil.copyfile(backup, temporary)
temporary.chmod(0o644)
os.replace(temporary, root / 'index.html')
PY
```

相应旧公开源码、Staging 静态目录和 manifest 保留在本次发布目录的 `rollback/` 中。
