# 首字延迟5秒问题分析

## 🔴 根本原因

### 问题1：`submitting` 标志阻止了 `useRunEvents` 启动

**位置：** `src/features/mono/MonoWorkspace.tsx:163`

```typescript
const visibleRunId = submitting ? null : currentRun?.sessionId === activeSessionId ? currentRun.runId : sessionRunId;
```

**问题：**
- 当用户点击发送后，`setSubmitting(true)` 立即执行
- `visibleRunId` 被设置为 `null`（因为 `submitting === true`）
- `useRunEvents(api, visibleRunId, onRunCompleted)` 接收到 `null`，**不会启动轮询**
- 直到 `api.sendMessage()` 完成并 `setSubmitting(false)` 后，`visibleRunId` 才有值
- 这期间可能经过 **3-5秒**（网络请求、服务器处理、消息刷新）

**流程时间线：**
```
T+0ms:    用户点击发送
T+0ms:    setSubmitting(true) → visibleRunId = null
T+0ms:    useRunEvents 看到 runId=null，不启动
T+50ms:   发送请求到服务器
T+500ms:  服务器创建 run，开始处理
T+800ms:  服务器返回 { run_id: "xxx" }
T+850ms:  setCurrentRun({ runId: "xxx" })
T+900ms:  invalidateQueries 触发消息刷新
T+1500ms: 消息刷新完成
T+1500ms: setSubmitting(false) → visibleRunId = "xxx"
T+1500ms: useRunEvents 现在才看到 runId，开始第一次轮询
T+1700ms: 第一次轮询返回，获取到事件
T+1700ms: 首字显示！
```

**总延迟：1.7秒（在最优情况下）**

### 问题2：消息刷新等待导致额外延迟

**位置：** `src/features/mono/MonoWorkspace.tsx:235-240`

```typescript
await Promise.all([
  queryClient.invalidateQueries({ queryKey: ["messages", user.id, targetSessionId] }),
  queryClient.invalidateQueries({ queryKey: ["sessions", user.id, targetProjectId] }),
  queryClient.invalidateQueries({ queryKey: ["usage", user.id] }),
  queryClient.invalidateQueries({ queryKey: ["usage-entries", user.id] }),
]);
```

**问题：**
- `invalidateQueries` 会触发重新获取消息列表
- 这是一个额外的网络请求（可能 500-2000ms）
- 在所有这些完成之前，`submitting` 保持 `true`
- `useRunEvents` 一直处于暂停状态

**实际流程：**
```
T+800ms:  服务器返回 run_id
T+850ms:  开始 invalidateQueries
T+900ms:  发送 GET /messages 请求
T+1500ms: 消息列表返回（包含用户消息）
T+1500ms: setSubmitting(false)
T+1500ms: useRunEvents 启动
T+1700ms: 首次轮询，获取 message.delta
T+1700ms: 显示首字
```

**问题3：200ms 轮询间隔也有影响**

即使 `useRunEvents` 及时启动，第一次轮询也需要等待：
- 如果刚错过一个轮询周期：+200ms
- 平均延迟：+100ms

### 问题4：后端可能的延迟

如果后端在 run 创建后才开始流式输出：
- Run 创建 → 数据库写入 → 消息队列 → Agent 启动 → 首个 token
- 可能增加 1-3秒

## 🎯 完整的延迟分析

**最坏情况时间线（5秒）：**
```
T+0ms:     用户点击发送
T+100ms:   POST /messages 网络延迟
T+500ms:   服务器创建 run，数据库写入
T+1000ms:  服务器返回 run_id
T+1100ms:  前端开始刷新消息
T+1200ms:  GET /messages 网络延迟
T+2000ms:  消息列表返回
T+2000ms:  setSubmitting(false)
T+2000ms:  useRunEvents 首次启动
T+2200ms:  第一次轮询（200ms 间隔）
T+2300ms:  GET /events 网络延迟
T+3000ms:  后端此时才开始生成首个 token（Agent 冷启动）
T+3500ms:  首个事件可用
T+3700ms:  第二次轮询获取到首个 delta
T+3800ms:  前端渲染
T+3800ms:  用户看到首字！
```

**总延迟：3.8秒**

如果后端 Agent 冷启动更慢，或者网络更慢：**可以达到5秒**

## ✅ 修复方案

### 修复1：立即启动 useRunEvents（高优先级）

**思路：**
- 在 `sendMessage` 返回 `run_id` 后立即设置 `currentRun`
- 不要等待消息刷新
- `useRunEvents` 可以立即开始轮询

**修改位置：** `src/features/mono/MonoWorkspace.tsx:230-234`

```typescript
// ❌ 之前
const result = await api.sendMessage(targetSessionId, payload, pendingSend.current.key, activeProjectId);
if (createdSession) completeComposerDraft(conversationDraftScope(user.id, targetSessionId), submittedDraft);
pendingSend.current = null;
setCurrentRun({ sessionId: targetSessionId, runId: result.run_id });
setSubmitting(false);
await Promise.all([
  queryClient.invalidateQueries({ queryKey: ["messages", user.id, targetSessionId] }),
  // ...
]);

// ✅ 修复后
const result = await api.sendMessage(targetSessionId, payload, pendingSend.current.key, activeProjectId);
if (createdSession) completeComposerDraft(conversationDraftScope(user.id, targetSessionId), submittedDraft);
pendingSend.current = null;

// 立即启动事件轮询，不等待消息刷新
setCurrentRun({ sessionId: targetSessionId, runId: result.run_id });
setSubmitting(false);

// 在后台异步刷新消息，不阻塞 useRunEvents
void Promise.all([
  queryClient.invalidateQueries({ queryKey: ["messages", user.id, targetSessionId] }),
  queryClient.invalidateQueries({ queryKey: ["sessions", user.id, targetProjectId] }),
  queryClient.invalidateQueries({ queryKey: ["usage", user.id] }),
  queryClient.invalidateQueries({ queryKey: ["usage-entries", user.id] }),
]);
```

**预期改善：减少 500-2000ms 延迟**

### 修复2：减少初始轮询间隔（中优先级）

**思路：**
- 第一次轮询使用更短的间隔（如50ms）
- 后续轮询恢复到200ms

**修改位置：** `src/features/chat/useRunEvents.ts:29-42`

```typescript
// ❌ 之前
const tick = async () => {
  try {
    if (api.streamRunEvents) {
      await api.streamRunEvents(runId, cursor, (event) => accept([event]), controller.signal);
    } else {
      accept(await api.getRunEvents(runId, cursor));
    }
  } catch (error) {
    if (cancelled) return;
    console.error("Run events fetch error:", error);
  }
  if (cancelled || terminal) return;
  const pollInterval = terminal ? 1200 : 200;
  timer = setTimeout(tick, pollInterval);
};

// ✅ 修复后
let pollCount = 0;
const tick = async () => {
  try {
    if (api.streamRunEvents) {
      await api.streamRunEvents(runId, cursor, (event) => accept([event]), controller.signal);
    } else {
      accept(await api.getRunEvents(runId, cursor));
    }
  } catch (error) {
    if (cancelled) return;
    console.error("Run events fetch error:", error);
  }
  if (cancelled || terminal) return;
  
  pollCount++;
  // 前3次轮询使用50ms间隔，之后使用200ms，结束后1200ms
  const pollInterval = terminal ? 1200 : pollCount <= 3 ? 50 : 200;
  timer = setTimeout(tick, pollInterval);
};
```

**预期改善：减少 0-150ms 延迟（首次轮询）**

### 修复3：优化消息刷新（中优先级）

**思路：**
- 只在必要时刷新消息
- 使用乐观更新，立即显示用户消息

**已完成：** `MonoWorkspace.tsx:221-228` 已经有乐观更新

**进一步优化：** 减少不必要的并行刷新

```typescript
// 只刷新必要的
void queryClient.invalidateQueries({ queryKey: ["messages", user.id, targetSessionId] });
void queryClient.invalidateQueries({ queryKey: ["sessions", user.id, targetProjectId] });
// usage 刷新可以延迟到 run 完成时
```

### 修复4：使用 SSE 而不是轮询（最优方案）

**思路：**
- 后端支持 Server-Sent Events
- 前端已有 `streamRunEvents` 实现
- 确保后端正确实现 SSE 端点

**检查：** `src/api/http.ts:176` 已有实现，确保后端支持

## 📊 修复效果预测

### 当前状态（5秒首字延迟）
```
T+0ms:     点击发送
T+1000ms:  服务器返回 run_id
T+2000ms:  消息刷新完成，setSubmitting(false)
T+2200ms:  首次轮询
T+3000ms:  后端生成首个 token
T+5000ms:  前端接收并显示首字
```

### 修复后（1-2秒首字延迟）
```
T+0ms:     点击发送
T+1000ms:  服务器返回 run_id
T+1000ms:  立即 setCurrentRun + setSubmitting(false)
T+1000ms:  useRunEvents 立即启动
T+1050ms:  首次轮询（50ms 间隔）
T+1100ms:  轮询返回（可能还没有事件）
T+1150ms:  第二次轮询
T+1200ms:  轮询返回（可能还没有事件）
T+1250ms:  第三次轮询
T+1300ms:  后端此时开始生成首个 token
T+1500ms:  第四次轮询获取到首个 delta
T+1500ms:  显示首字！
```

**总延迟：1.5秒（减少 70%）**

如果后端更快或使用 SSE：**可降至 800ms-1秒**

## 🚀 实施优先级

1. **立即修复** - 移除 `await` 在消息刷新前（减少 1-2秒）
2. **立即修复** - 减少前几次轮询间隔到50ms（减少 150ms）
3. **中期优化** - 确认后端 SSE 实现（可减少到接近实时）
4. **长期优化** - 后端优化 Agent 冷启动（减少后端延迟）

---

**生成时间：** 2026-10-07  
**问题严重程度：** 🔴 严重 - 影响核心用户体验  
**预期修复时间：** 30分钟  
**预期效果：** 首字延迟从 5秒降至 1-1.5秒（70%改善）
