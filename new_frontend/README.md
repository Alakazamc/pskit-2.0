# Research Agent 前端

独立的 React + Vite + TypeScript SPA。前端始终通过 `/api/v1` 调用 HTTP 接口；开发时由 `new_backend/` 的 mock 模式提供同一份 API 契约，不在浏览器里复制任务和配额逻辑。

新版前后端的独立 Docker 联调启动与验证命令见 [`../deploy/agent/README.md`](../deploy/agent/README.md)。该入口运行 Python/Pi 与模型替身，网页地址为 <http://127.0.0.1:18085>。

## 启动

先启动 mock API：

```bash
cd new_backend
python -m pip install -e ".[dev]"
python -m uvicorn app.main:app --host 127.0.0.1 --port 18080
```

再启动前端：

```bash
cd new_frontend
npm ci
npm run dev
```

打开 http://localhost:5174，使用开发邮箱登录。项目、会话、技能、资源、消息、事件和配额都从后端接口读取。`/` 和 `@` 选择器只列出接口实际返回的条目；当前服务端提供结构分析 Skill 和 PDB/UniProt MCP mock 资源。文本文件与可提取文字的 PDF 会作为原始文件流上传到 Python。

界面提供简体中文和 English。登录页可直接切换；登录后从左侧「设置 / Settings」进入语言设置。选择保存在当前浏览器，刷新后继续生效。导航、登录、会话、Composer、Agent 面板、额度及已知 API 错误跟随界面语言；项目名、用户输入、模型回答和其他服务端提供的数据保持原文。
当前独立 `/artifacts` 页面通过 Python API 列出当前用户产物。只有服务端确认保存了文件字节的产物可以下载；仅有示例元数据的 mock 产物会显示为不可下载。新版 Mono 工作台的导航、设置、项目、工具、PDB 检索、产物和输入框文案已接入中英切换；服务端返回的项目名、Skill 描述和工具描述按原文显示。
`/tools` 只显示 `/api/v1/mcp/tools` 当前返回的工具；没有服务端能力时显示空状态。未接入的 INABe/AF3 预测占位页已移除。PDB 仍有结构检索专页，其他 MCP 工具可从公布的 `input_schema` 生成基础参数表单并调用统一 API。复杂 JSON Schema 的可视化编辑仍需扩展，后端始终负责最终参数校验。

## 接口配置

默认开发代理将 `/api` 转发到 `http://127.0.0.1:18080`。需要改目标地址时，启动 Vite 前设置 `DEV_API_PROXY_TARGET`；浏览器 API 路径可在 `.env.local` 设置：

```dotenv
VITE_AUTH_MODE=demo
VITE_API_BASE_URL=/api/v1
```

开发模式未设置 `VITE_AUTH_MODE` 时可使用演示登录；生产构建未设置时默认走 Python 的 Supabase 登录接口，不会隐式显示演示登录。需要发布演示环境时须显式设置 `VITE_AUTH_MODE=demo`。

Python 后端切换到 Pi 模式时，前端的 `ResearchApi` 接口和页面都不需要修改。

HTTP 类型从 `../contracts/openapi.json` 生成：后端改动后，先在 `new_backend/` 执行 `PYTHONPATH=. python scripts/export_openapi.py`，再在本目录执行 `npm run generate:api`。生成文件位于 `src/api/generated/`，身份、项目、会话、用量、文件与产物 DTO 已在 `src/api/types.ts` 中复用，部分路由响应由 `src/api/contract-typecheck.ts` 检查。SSE 事件仍使用独立的 `../contracts/run-event.schema.json` 与手写前端判别式类型。

真实身份模式使用：

```dotenv
VITE_AUTH_MODE=supabase
VITE_API_BASE_URL=/api/v1
# 当 Python live 模式启用游客注册和 CAPTCHA 时：
VITE_TURNSTILE_SITE_KEY=your-public-site-key
```

邮箱密码提交到 Python 登录接口，Google OAuth 从 Python 的 `/auth/google/start` 发起并由 Python 完成回调。浏览器不初始化 Supabase SDK；New API 用户令牌和 GPU 凭据只配置在 Python 服务端，不能放入 `VITE_*` 变量。开发代理必须覆盖 `/api/v1/auth/google/callback`；跨域部署需由反向代理保持认证 Cookie 与 `/api/v1` 同源。
游客入口在点击后才向 Python 创建匿名身份；配置 Turnstile 站点密钥后，必须先完成人机验证，前端将一次性令牌提交给 Python 的 `/auth/anonymous`。Supabase 中需开启匿名登录与 Turnstile CAPTCHA，验证密钥只配置在 Supabase，不放入前端环境变量。本机自托管 Supabase 已完成普通邮箱认证联调；游客 CAPTCHA 挑战与升级流程仍需配置后联调。

## 目录

| 目录 | 职责 |
| --- | --- |
| `src/app/` | 路由、Query Provider、登录状态 |
| `src/api/` | 类型契约、HTTP/SSE 客户端 |
| `src/features/auth/` | 演示登录及 Python 邮箱/Google 登录界面 |
| `src/features/workspace/` | 导航、项目侧栏、工作台布局 |
| `src/features/chat/` | 分段消息、Composer、事件归并与服务端任务展示 |
| `src/features/usage/` | Token 月限额与每日 GPU 展示 |
| `src/i18n/` | 中英界面文案、语言偏好与已知错误码映射 |
| `src/styles/` | 主题、各工作区区域和响应式样式 |

TanStack Query 保存服务端快照，Zustand 只保存 Composer 临时上下文。聊天渲染保留独立组件边界，后续可以接入 assistant-ui primitives。文件上传支持 1 MiB 以内的 UTF-8 文本、Markdown、CSV、TSV、JSON、FASTA，以及 10 MiB、100 页以内可提取文字的 PDF；Pi 模式会读取选中的文件内容，mock Agent 仍返回演示回复。扫描版 PDF 尚无 OCR。

## 验证

```bash
npm test
npm run typecheck
npm run lint
npm run build
```

模型选择器的浏览器回归脚本位于 `tests/browser/model_picker.py`。在安装了 Python Playwright 和 Chromium 的环境中，保持前端运行后执行：

```bash
python tests/browser/model_picker.py --base-url http://localhost:5174
```

可用 `--browser-executable` 指定已有 Chromium，用 `--screenshots` 指定截图目录。脚本拦截认证、模型目录和聊天 API，使用合成数据，检查搜索框焦点边框、向上拖动推理推杆、手机触摸、键盘档位、模型切换重置和实际发送参数，不调用真实模型。覆盖中英、深浅主题及桌面、手机宽度。

后端默认 mock 使用进程内状态，重启后重置；当前 Skill 和 MCP mock 资源来自服务端目录。启用后端 `RESEARCH_AGENT_RUNTIME=pi` 后，消息、Run、事件和 AF3 mock 任务持久化到 SQLite；前端刷新时会从会话最近一次 Run 恢复进度订阅。模型调用需要服务端配置 Pi 凭据；Token 用量优先采用模型回报值，缺失时按输入估算。
