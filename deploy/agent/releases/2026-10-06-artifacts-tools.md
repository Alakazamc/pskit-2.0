# 2026-10-06 对话产物与 Tools 页面发布

生产入口：[Agent](https://agent.bioailab.net/)、[Tools](https://agent.bioailab.net/tools)。2026-10-05 晚先发布对话产物接口与右上角入口；2026-10-06 凌晨再发布 Tools 卡片和详情布局。前端构建参数为 `VITE_AUTH_MODE=supabase VITE_API_BASE_URL=/api/v1`。

## 固定制品

| 项目 | 对话产物 | Tools 布局 |
| --- | --- | --- |
| 源码提交 | `d9e3f5419eeddcc6469b9a33b35154222d8dc5a3` | `d8e8c64b47c6546e94b58180a45b0618b6ad0502` |
| 阿里云 release 目录 | `/home/ecs-user/pskit-agent-releases/20261005-artifacts-d9e3f54` | `/home/ecs-user/pskit-agent-releases/20261006-tools-d8e8c64` |
| 前端 dist 内容哈希 | `daed8ebf4aea3c0c4e493b528c622aa5d21c1dd1b22283eb5c9726dc8923ad7f` | `6a496da53734a4778adb50b6e81fdffd798fb1b7f5913b06221f9ee4dfa3b5ae` |
| `index.html` SHA256 | `d8e8a310d9d0fbfe81013d395255bd7435f86ae49374461c49352f81c53ba20d` | `4d9999d6c7fdab73ce62566ee24e1cc77cb2fe0347ab0e9b4377d45a22000017` |
| 源码归档 SHA256 | `19b0c6cde3ecf76bd0e3e1edbf7a16c1a43da1b7e44bfa8ec794e767539d96dc` | `a3136145477dac9b64dbcb1df8d61c6c5123beee8ac67e1e448d7f5baeb69472` |
| dist 归档 SHA256 | `fc7d6a18b424422e010bd84ad2484f2d1c8fca642de1b670a96f21d8106c1b91` | `8c737ace77abcfb1f7f2951087ef43673c9bc0ff9eb6ad7b1b2c2951720964d2` |

后端镜像是 `pskit-agent-backend:20261005-artifacts-d9e3f54`，ID `sha256:171bca4bb7af9b61ba4617799b8e8a0551bc00100e5c950f4bd078283a38b758`。镜像归档 SHA256 为 `bbf4739a9961f46c7b3be87a620f6aa68e3a985617670b953e980e6b067070dd`。阿里云 Docker Hub 认证请求超时，因此在本地按固定 Dockerfile 构建，再传输归档；远端 SHA256 和镜像 ID 与本地一致。Tools 发布仅替换静态 dist，没有重建后端、PostgreSQL、Supabase、LiteLLM 或 A6000 接收器。

## 验收

- 对话产物发布前，生产 `/api/v1/sessions/example/artifacts` 返回 404；发布后未登录请求返回 401。Staging 合成账号读取本会话产物列表返回 200，未登录 401，未知会话 404。前端测试覆盖侧栏无产物入口、右上角按钮、单一“产出”列表、预览与下载。
- 两次固定制品都先在隔离 Staging 验收。登录、文件、SSE、Token/GPU 配额和模拟 AF3 烟测通过；第二次从私网 Nginx 入口验收，入口 `index.html` 哈希与制品相同。Staging 测试后停止专属 Compose 项目，保留账号与卷。
- Tools 前端 `npm test`：37 个文件、230 项通过；`npm run typecheck`、`npm run lint` 和生产构建通过。测试覆盖卡片大尺寸变体、整页详情、返回焦点、单工具历史、CORAL 和结构查看器。浏览器中的实际像素布局仍需用户目视确认。
- 生产 `/tools` 的 `index.html` SHA256 与发布制品一致，入口引用的 3 个静态资源经 HTTPS 按内容哈希核对一致。生产 ready 与 `/login` 返回 200，未登录 usage 与会话产物接口返回 401，`/internal/` 返回 404，公网管理页返回 403。
- 后端切换前与停止后均确认没有未完成 Agent Run 或 Job。未做数据库结构迁移，也未复制或清空生产数据。

## 回退

两个 release 目录均保存切换前完整生产静态目录归档；对话产物 release 另存切换前 `cloud.env` 和旧镜像 `pskit-agent-backend:20261005-coral-3e5accb` 的 ID `sha256:8b26bb6ff078f8f015d740130c4cf1fd562fa27353fb06fa1c1138cce564c50e`。仅回退 Tools 布局时，先核对 `/home/ecs-user/pskit-agent-releases/20261006-tools-d8e8c64/production-dist-before.tar.gz`，再恢复静态文件；后端保持当前版本。回退产物功能时须同时恢复旧前端、旧后端镜像 pin，并先等待 Run/Job 空闲。不得恢复旧数据库覆盖上线后的用户写入。
