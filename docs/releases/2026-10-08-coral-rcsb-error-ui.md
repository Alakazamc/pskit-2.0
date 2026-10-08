# CORAL RCSB 网络与结构化错误展示修复发布

日期：2026-10-08。前端修复提交：`64ca5b1`；运维说明提交：`6daeca1`。

## 故障与根因

用户提交 `6VXX / A / 1 条 / 50 nt` 的 CORAL one-shot 工作流后，计算服务在读取 `https://files.rcsb.org/view/6VXX.cif` 时连接被拒绝。失败已按服务上报记录 1,323 ms wall time、878 CPU core-ms 和 1,313 GPU device-ms，并进入终态；中央没有遗留活动或待对账 Job。

4090 和 A6000 直连 RCSB 均被拒绝；4090 的其他 HTTPS 请求正常。本机 `127.0.0.1:1080` SOCKS 代理能够从 CORAL 实际 Python 环境读取同一 mmCIF，因此在 4090 私有 `.pskit-mcp.runtime.env` 中加入 `HTTPS_PROXY=socks5h://127.0.0.1:1080`，并用 `NO_PROXY` 保留 localhost、WireGuard 和模型内网流量。代理配置未写入 Git，也未导出凭据。

前端 `MetricGrid` 原先对嵌套对象执行 `String(value)`，使 `{code, message}` 错误显示为 `[object Object]`。现在优先展示 `code: message`，其余结构化值显示有长度上限的 JSON，并允许长错误信息换行。

## 提供端验证

- 4090 CORAL provider PID：`3496099`；进程已继承私有 SOCKS 代理与 `NO_PROXY`。
- 使用 `/home2/jhli/anaconda3/envs/rbpsdiff_tmp/bin/python` 和实时私有运行环境读取精确地址，返回 HTTP 200、2,560,152 字节，内容以 `data_` 开头。
- A6000 通用接收器仍发现四个工具：`coral.generate.one_shot`、`coral.generate.iterative`、`coral.analyze.pocket`、`coral.optimize.two_dimensional`。
- 本次未重新执行 CORAL 推理，避免未经用户触发再次消耗 GPU 配额。

## 前端制品

| 项目 | 值 |
| --- | --- |
| 阿里云发布目录 | `/home/ecs-user/pskit-agent-releases/20261008-coral-error-ui-64ca5b1` |
| 归档 SHA256 | `78d25cac37c20407cf1548732c5235cef314df1ae02c9c2f070ee89cfe6a05ff` |
| 完整 dist SHA256 | `873ee9a5b06416dc6004f01f8bf7b3ae0ce561239fe19cccf9fdfa04269d975d` |
| index SHA256 | `50f8a895273f66cf232d42aa361d7072d38b78b03be515cc5cc188070a07eb48` |
| 文件数 | 401 |
| 生产 Turnstile 公钥 SHA256 | `91c177d7f198741dbd53193a4555987bc5ac5c40a4c74cb96101e403aeaa3c1a` |

生产参数构建通过 typecheck、Vite build 和 Molstar 分包检查。制品先发布到私网 Staging；入口 HTML 与四个首屏资源逐字节一致，页面 200、未登录 usage 401、internal 404、后端 readiness 200。随后备份完整生产静态目录，先复制资源，最后原子替换 `index.html`。

生产验收：登录页 200、未登录 usage 401、internal 404、公网管理入口 403、后端 readiness 200；入口 HTML 和四个首屏资源均与同一制品一致。后端镜像、数据库、Nginx 配置、A6000 接收器和 AF3 容器均未修改。

## 回滚

Staging 与生产的完整静态备份分别位于发布目录的 `rollback/staging-static` 和 `rollback/production-static`。回滚生产前端时复制旧资源，并将旧 `index.html` 写入同目录临时文件后用 `os.replace` 原子恢复；无需 reload Nginx。4090 提供端的私有运行环境备份位于 `/data/jhli/project/annoy-coral-pskit-mcp/.pskit-mcp.runtime.env.pre-rcsb-proxy-20261008`，只有确认不再需要 RCSB 访问时才恢复并重启 CORAL MCP。
