# 2026-10-04 模型搜索框与推理推杆发布

生产入口：[agent.bioailab.net](https://agent.bioailab.net)。发布校验完成于北京时间 2026-10-04 15:26。

## 修改

- 模型搜索框保持无边框，焦点用行背景提示；修复通用 `input:focus-visible` 样式覆盖局部规则而产生矩形边框的问题。
- 推理强度改为竖直推杆，向上增强；显示当前模型公布的档位，支持鼠标、触摸及方向键、Home/End。模型切换重置为默认，所选值继续通过 `reasoning_effort` 发送。
- 模型菜单和箭头向上，提供中英文提示。规则记录在 `new_frontend/AGENTS.md`。

## 制品

| 字段 | 值 |
| --- | --- |
| Release | `20261004-thinking-lever-10f90bd` |
| 前端源码提交 | `10f90bdf795090d8d7c4268adcdc526938682832` |
| 构建参数 | `VITE_AUTH_MODE=supabase VITE_API_BASE_URL=/api/v1` |
| dist SHA256 | `ab160307b79cb7e2327ce35522765c8648da3e6ff570cf5cc86bb5e1a0d22d38` |
| index SHA256 | `2a349634f1677281f3a9b8f89073b1478ec8775182b7431ffe6695e8f8c7218f` |
| 传输归档 SHA256 | `7dfecc030c2649a2d823b7f0e9509e273cee9680a761e141631fbec4d2bd6568` |
| 保留的后端镜像 | `pskit-agent-backend:20261004-composer-8dce6b8b` |
| 后端镜像 ID | `sha256:925853652b745c1ec22a60a9be409f469f58ec2478fd8ef5341cfbb58c030269` |

阿里云发布目录为 `/home/ecs-user/pskit-agent-releases/20261004-thinking-lever-10f90bd`，保留版本化前端源码、传输归档、dist、release.json 和发布前的完整静态目录。本次只更新前端，不重建或重启生产后端；其 StartedAt 仍为 `2026-10-04T06:38:18.101255039Z`。生产数据库、密钥、Pi 与 A6000 拓扑保留。

## 验收

- TDD 先复现旧界面找不到推理 slider，以及真实浏览器焦点 `outline=solid`、搜索行 `border=1px` 的问题，再完成修复。
- 前端测试 148 passed；typecheck、lint、生产 build 通过。现有 Molstar/高亮代码块体积及浏览器模块外置提示仍存在。
- 维护的 `new_frontend/tests/browser/model_picker.py` 分别在开发前端、生产构建和生产域名通过四种组合：dark/zh/1360×860、light/en/1360×860、dark/en/390×860、light/zh/390×600。检查实际焦点样式、向上鼠标或触摸拖动、键盘档位、模型切换重置、菜单向上及不越界，以及 `model + reasoning_effort=high` 消息请求。无页面异常。
- 浏览器验收拦截 API 为合成数据，实际加载目标站点的 JS/CSS；不创建生产测试账号或调用付费模型。本次没有重复真实模型联调，后端能力和调用协议未改动。
- Staging 复用已有私有配置、测试卷和固定后端镜像，锁定本次 dist。通过实际私网 Nginx 完成登录、文件、SSE、额度与模拟 AF3 烟测；生产快照未变。
- Staging 首次启动发现两份只读挂载的公开源码被设为 `0600`：`provision_shared_postgres.py` 和 `mock_model_gateway.py`。容器中的 UID 10001 无法读取。仅将这两份公开源码恢复为 `0644`，保留私有配置权限；停止部分启动的 Staging 项目后重试成功。该权限要求已补入部署手册。
- 生产先复制 assets，再原子替换 index，保留旧 assets；386 份新制品文件及 HTTPS index、三个入口资源的内容一致。登录 200、就绪 200、未登录 usage 401、internal 404。没有改 Nginx 配置、reload 或要求 sudo。
- 静态验收后同步源码时发现目标 `new_frontend/` 尚未创建，补齐父目录后完成同步并重新校验页面与资源；发布目录保存完整制品。
- Staging 验收后恢复为停止状态，配置及数据卷保留。

## 前端回滚

旧完整静态目录位于发布目录下的 `production-static-before/`，权限 0700；旧 index SHA256 为 `8c6e16a32760230ff0ddd6c69063533520159e9e6a2e994ea02f800b6e18aac9`。旧 assets 已保留。需要回滚时在阿里云执行：

```bash
release=/home/ecs-user/pskit-agent-releases/20261004-thinking-lever-10f90bd
install -m 0644 "$release/production-static-before/index.html" \
  /var/www/agent.bioailab.net/.index.rollback
mv /var/www/agent.bioailab.net/.index.rollback /var/www/agent.bioailab.net/index.html
curl --fail -I https://agent.bioailab.net/login
```

回滚仅恢复前端入口，保留数据库及新的用户写入，不更换后端镜像。
