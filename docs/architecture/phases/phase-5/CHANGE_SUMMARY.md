# Phase 5：前端拆分、历史分页与草稿隐私

模型显示使用服务器 display_name/provider_label，不再猜测 alias 前缀；没有元数据时保留完整旧 alias。手动能力声明/受控验收/结果审核/发布由现有 ModelPolicy 管理；验收按钮只提交 revision/reason，不能自己填写通过结果。

MonoWorkspace 分拆查询、发送、会话/Run生命周期、Sidebar。SSE原始event最多512条，累计文本、工具、产物、任务、终态单独投影，不能随原始缓存截断丢失。工具完成、上传不定进度与现有文件卡片继续复用。

新增可选 messages/page 路由，旧 messages 列表和旧客户端语义不变。PG仅物化limit+1；owner/session/project/foreign cursor检查一致；只在旧服务器404/405时fallback旧list，403不fallback。前端保留旧messages QueryKey，分页去重并保留乐观提交、expanded状态和可见行anchor。相同时间戳依服务端ordinal顺序，不能按opaque ID重新排列；多设备产生的新gap及并发cursor回归已纳入。

浏览器草稿v1格式不变，设置增加保留说明、停止持久化、按会话/账号清理和退出清理。旧草稿不会自动删除，云端不存草稿/模型偏好；没有存access token或blobURL进草稿。停止持久化不等同自动删除已有副本，清理使用显式按钮。

主题变量集中styles/tokens.css并保留旧alias。没有加入虚拟列表或新增UI框架。管理页、Artifact、结构预览仍沿用现有入口。

## 主要代码边界

`features/mono/MonoWorkspace.tsx` 保留壳，拆出 `useWorkspaceQueries.ts`、`useConversationSend.ts`、`useConversationLifecycle.ts`、`WorkspaceSidebar.tsx`；消息分页/顺序与scroll在 `features/chat/useMessageHistory.ts`、`Conversation.tsx`、`useChatAutoscroll.ts`。草稿设置是 `composerDrafts.ts` / `BrowserDraftSettings.tsx`，语义源为 `styles/tokens.css`。`ModelRuntimeProfileEditor.tsx` 复用现有管理revision/资格/发布流程，`api/admin.ts` 仅增量接线，不新建管理前后端。
