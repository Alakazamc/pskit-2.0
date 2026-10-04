# 2026-10-04 紧凑推理面板发布

生产入口：[agent.bioailab.net](https://agent.bioailab.net)。2026-10-04 19:16（Asia/Shanghai）发布，19:22 完成验收。

## 修改与原因

模型名称与当前推理强度放在输入框右下角、发送或停止按钮旁边。推理设置向上展开，使用竖直飞机推杆；面板缩至 88 × 122 px，内边距 4 px，保留 44 px 鼠标和触摸拖动目标。强度文字固定居中，下方的向上箭头用于打开模型选择。

模型搜索沿用当前主题，不显示白色输入框或粗焦点边框。尾焰随实际推理等级采用烟蓝、灰紫、浅琥珀色，使用淡色核心和透明渐隐尾部；默认和关闭等级不显示尾焰。不同模型的相同等级使用相同颜色。支持中英文、深浅主题、触摸及键盘操作；模型与 reasoning_effort 继续通过现有请求传给后端，切换模型恢复默认强度。界面规则已写入 new_frontend/AGENTS.md。

## 制品

| 字段 | 值 |
| --- | --- |
| Release | `20261004-composer-throttle-7e0221c` |
| 前端源码提交 | `7e0221c1557d52778b02bc01856f667a125c295e` |
| 构建参数 | `VITE_AUTH_MODE=supabase VITE_API_BASE_URL=/api/v1` |
| dist SHA256 | `b310f38fb66cde533547acb5678353c2de354f4b01b1873bef98abe16a7f76b5` |
| index SHA256 | `8190ed1e6e6c1e85c571cb3b3204e0f0fb33340804f4fe050ed2c187be4e00e6` |
| 传输归档 SHA256 | `f0d20933f856419324f1cf87d5a724eb0a522ef7e6d958bd9c67f12f8437c69e` |
| 保留后端镜像 | `pskit-agent-backend:20261004-composer-8dce6b8b` |
| 后端镜像 ID | `sha256:925853652b745c1ec22a60a9be409f469f58ec2478fd8ef5341cfbb58c030269` |

版本目录：`/home/ecs-user/pskit-agent-releases/20261004-composer-throttle-7e0221c`。保存前端源码、dist、传输归档、release.json、Staging 验收记录和发布前完整静态目录。

## 验收

- 前端 25 个测试文件、154 项测试通过；typecheck、lint 和显式生产参数 build 通过。
- 模型选择浏览器检查在本地生产构建和正式域名各通过四组：dark/zh/1360×860、light/en/1360×860、dark/en/390×860、light/zh/390×600。检查右下角入口、窄面板、居中强度、下方箭头、搜索焦点、尾焰配色和渐隐、鼠标与触摸拖动、键盘操作、模型切换以及模型与强度请求。
- 正式域名流式回复浏览器检查通过同样四组尺寸和主题：回复左上角加载图标、首个事件前等待、Markdown 高度增长、自动跟随底部、保留手动阅读位置、完成、停止按钮与取消。
- 浏览器加载生产实际 JS/CSS，API/SSE 使用合成数据；没有创建生产测试账号，也没有调用付费模型或 GPU。真实登录、文件上传、SSE、额度及模拟 AF3 流程在隔离 Staging 通过，生产数据快照未变。Staging Nginx 的 index 和三个入口资源与制品一致。
- 生产发布先备份完整静态目录，再复制新 assets，原子替换 index，并保留旧 assets。386 份新文件及三个入口资源与制品逐字一致。最终 HTTPS 登录 200、未登录 usage 401、internal 404、后端就绪 200。
- 生产后端 StartedAt 为 `2026-10-04T06:38:18.101255039Z`；镜像、数据挂载和 Nginx 配置哈希与发布前一致。发布后 Staging 已停止，保留独立配置和测试卷。

## 前端回滚

发布前完整静态目录位于版本目录下的 `production-static-before/`，权限 0700；旧 index SHA256 为 `9adf056c35519739c24d8fc96825b97cef5dcb70ee4699b4ea14abbbc5e25796`。旧 assets 保留。在阿里云执行：

```bash
release=/home/ecs-user/pskit-agent-releases/20261004-composer-throttle-7e0221c
install -m 0644 "$release/production-static-before/index.html" \
  /var/www/agent.bioailab.net/.index.rollback
mv /var/www/agent.bioailab.net/.index.rollback /var/www/agent.bioailab.net/index.html
curl --fail -I https://agent.bioailab.net/login
```

回滚只恢复前端入口，保留用户新写入的数据。
