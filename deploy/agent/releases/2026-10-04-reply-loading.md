# 2026-10-04 流式回复左上角加载图标发布

生产入口：[agent.bioailab.net](https://agent.bioailab.net)。

## 修改与原因

录屏中，主加载图标位于 Markdown 正文后的复制操作栏，回复增长时图标随正文移到下方。现在助手回复顶部保留 28 px 图标位：等待首个 SSE 事件和持续输出时转圈；完成、失败、取消或等待审批时显示静态图标。已保存回复使用相同占位，避免结束时正文位移。

页面已知 Run 正在执行但尚无事件时，向 Conversation 明确传递等待状态；历史 Run ID 本身不会触发加载图标。Composer 的停止按钮和现有自动滚动行为保持同一 Run 生命周期。规则写入 `new_frontend/AGENTS.md`。

## 制品

| 字段 | 值 |
| --- | --- |
| Release | `20261004-reply-loading-1b00eee` |
| 前端源码提交 | `1b00eee1893322eefe3efd05060b5061df0bd8bd` |
| 构建参数 | `VITE_AUTH_MODE=supabase VITE_API_BASE_URL=/api/v1` |
| dist SHA256 | `4bb348afd01e4bb9bce162dbfaba3f2f62762636378b367f2c4852d4c30117d5` |
| index SHA256 | `9adf056c35519739c24d8fc96825b97cef5dcb70ee4699b4ea14abbbc5e25796` |
| 传输归档 SHA256 | `fb2ad90c300165f25f989343f39ee2da0cb442df02bdd7d5b7e6a61798ef0779` |
| 保留后端镜像 | `pskit-agent-backend:20261004-composer-8dce6b8b` |
| 后端镜像 ID | `sha256:925853652b745c1ec22a60a9be409f469f58ec2478fd8ef5341cfbb58c030269` |

版本目录：`/home/ecs-user/pskit-agent-releases/20261004-reply-loading-1b00eee`。保留前端源码、dist、传输归档、release.json 及发布前完整静态目录。

## 验收

- TDD 首先复现两处失败：加载图标不在回复顶部，以及重新打开执行中的会话在首个事件前没有回复加载图标。
- 前端 153 项测试、typecheck、lint 和生产 build 通过。现有 Molstar/高亮代码块体积及浏览器模块外置提示仍存在。
- 维护脚本 `new_frontend/tests/browser/streaming_reply.py` 在本地生产构建和公网实际 JS/CSS 分别通过 dark/zh/1360×860、light/en/1360×860、dark/en/390×860、light/zh/390×600。检查首个事件前的等待、加载图标相对回复左上角的位置、Markdown 与代码块高度增长、自动跟随底部、手动向上阅读、完成占位、消息快照接替以及停止按钮。没有页面异常。
- 浏览器使用合成 API 和可控 SSE，不创建生产测试账号或调用付费模型。本次后端协议及模型调用未改动。
- Staging 复用已有私有配置和测试卷，沿用固定后端镜像，锁定本次 dist。通过实际 WireGuard Nginx 完成登录、文件、SSE、配额及模拟 AF3 烟测；生产快照未变。Staging Nginx 返回的 index 与三个入口资源和制品一致。
- 生产先备份完整静态目录，再复制新 assets，原子替换 index，保留旧 assets。386 份新文件及公网 index、三个入口资源逐字一致。HTTPS 登录 200、就绪 200、未登录 usage 401、internal 404。
- 生产后端 StartedAt 仍为 `2026-10-04T06:38:18.101255039Z`，镜像及数据卷保留；未改 Nginx 路由或重启生产服务。Staging 验收后恢复停止状态，保留配置与测试卷。

## 前端回滚

发布前完整目录位于版本目录下的 `production-static-before/`，权限 0700；旧 index SHA256 为 `2a349634f1677281f3a9b8f89073b1478ec8775182b7431ffe6695e8f8c7218f`。旧 assets 保留。需要回滚时，在阿里云执行：

```bash
release=/home/ecs-user/pskit-agent-releases/20261004-reply-loading-1b00eee
install -m 0644 "$release/production-static-before/index.html" \
  /var/www/agent.bioailab.net/.index.rollback
mv /var/www/agent.bioailab.net/.index.rollback /var/www/agent.bioailab.net/index.html
curl --fail -I https://agent.bioailab.net/login
```

此回滚恢复前端入口，保留用户新写入的数据。
