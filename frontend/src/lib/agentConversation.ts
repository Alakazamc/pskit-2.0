import { computed, ref } from "vue";

import {
  ApiError, activeAgentTurn, api, createClientId, recoverAgentTurn,
  streamAgentMessage, streamErrorMessage,
  type ActiveAgentTurn, type AgentMessage, type AgentSession, type AgentStreamResult, type StreamEvent, type Task,
} from "./api";

type ArtifactEvent = { artifact_id: string; filename?: string; download_url?: string; recorded_at?: string };
type SourceEvent = { source?: string; heading?: string; score?: number; content?: string };
type Approval = { approval_id: string; tool_name: string; message?: string; argument_keys?: string[] };
type ToolEvent = { type: string; name?: string; tool_call_id?: string; task_id?: string; task_type?: string; status?: string };

function asApproval(value: unknown): Approval | null {
  if (!value || typeof value !== "object") return null;
  const item = value as Partial<Approval>;
  return typeof item.approval_id === "string" && /^[0-9a-f]{64}$/.test(item.approval_id)
    && typeof item.tool_name === "string" ? item as Approval : null;
}

const taskPageSize = 100;

function mergeTasks(current: Task[], incoming: Task[]): Task[] {
  const byId = new Map(current.map((task) => [task.id, task]));
  for (const task of incoming) {
    const previous = byId.get(task.id);
    if (!previous || Date.parse(task.updated_at) >= Date.parse(previous.updated_at)) {
      byId.set(task.id, task);
    }
  }
  return [...byId.values()].sort((left, right) =>
    Date.parse(right.created_at) - Date.parse(left.created_at) || right.id.localeCompare(left.id));
}

export function createAgentConversation(onMessagesChanged: () => void = () => undefined) {
  const sessions = ref<AgentSession[]>([]);
  const activeSessionId = ref("");
  const messages = ref<AgentMessage[]>([]);
  const input = ref("");
  const loading = ref(true);
  const sending = ref(false);
  const error = ref("");
  const notice = ref("");
  const taskError = ref("");
  const sources = ref<SourceEvent[]>([]);
  const ragBackend = ref("none");
  const messageArtifacts = ref<ArtifactEvent[]>([]);
  const events = ref<ToolEvent[]>([]);
  const allTasks = ref<Task[]>([]);
  const hasMoreTasks = ref(false);
  const loadingMoreTasks = ref(false);
  const streamingAnswer = ref("");
  const suggestions = ref<string[]>([]);
  const pendingApproval = ref<Approval | null>(null);
  const activeSession = computed(() => sessions.value.find((item) => item.id === activeSessionId.value));
  const tasks = computed(() => allTasks.value.filter((task) => task.session_id === activeSessionId.value));
  const followUpTask = computed(() => {
    const lastUserMessage = [...messages.value].reverse().find((message) => message.role === "user");
    if (!lastUserMessage) return null;
    const lastUserAt = Date.parse(lastUserMessage.created_at);
    if (!Number.isFinite(lastUserAt)) return null;
    return tasks.value
      .filter((task) => ["succeeded", "failed"].includes(task.status)
        && Date.parse(task.finished_at || "") > lastUserAt)
      .sort((left, right) => Date.parse(right.finished_at || "") - Date.parse(left.finished_at || ""))[0] || null;
  });
  const artifacts = computed(() => {
    const sorted = [
      ...messageArtifacts.value,
      ...tasks.value.flatMap((task) => task.artifacts.map((artifact) => ({
        artifact_id: artifact.id, filename: artifact.filename, download_url: artifact.download_url,
        recorded_at: task.finished_at || task.updated_at,
      }))),
    ].sort((left, right) => Date.parse(right.recorded_at || "") - Date.parse(left.recorded_at || ""));
    const newest = new Map<string, ArtifactEvent>();
    for (const artifact of sorted) {
      if (!newest.has(artifact.artifact_id)) newest.set(artifact.artifact_id, artifact);
    }
    return [...newest.values()];
  });
  const drafts = new Map<string, string>();
  let revision = 0;
  let disposed = false;
  let subscription: AbortController | undefined;
  let tasksRequest: { sessionId: string; version: number; promise: Promise<void> } | undefined;
  let moreTasksRequest: { sessionId: string; version: number; promise: Promise<void> } | undefined;
  let nextTaskOffset = 0;
  let hasLoadedOlderTasks = false;
  let firstPageArrivalVersion = 0;

  function isCurrent(sessionId: string, version: number) {
    return !disposed && activeSessionId.value === sessionId && revision === version;
  }

  function detach() {
    revision += 1;
    subscription?.abort();
    subscription = undefined;
    sending.value = false;
  }

  async function loadSessions() {
    const loaded = await api.sessions();
    if (!disposed) sessions.value = loaded;
  }

  function loadTasks(): Promise<void> {
    const sessionId = activeSessionId.value;
    if (disposed || !sessionId) return Promise.resolve();
    // Polling and stream events share a single request so slow responses cannot
    // overwrite newer task state or accumulate a backlog for one session.
    if (tasksRequest?.sessionId === sessionId && tasksRequest.version === revision) return tasksRequest.promise;
    const version = revision;
    const promise = api.tasks({ sessionId, limit: taskPageSize + 1, offset: 0 }).then((loaded) => {
      if (!isCurrent(sessionId, version)) return;
      const knownIds = new Set(allTasks.value.map((task) => task.id));
      const recent = loaded.slice(0, taskPageSize);
      const hasNewArrival = recent.some((task) => !knownIds.has(task.id));
      if (hasNewArrival) firstPageArrivalVersion += 1;
      allTasks.value = mergeTasks(allTasks.value, recent);
      if (!hasLoadedOlderTasks) {
        nextTaskOffset = Math.min(loaded.length, taskPageSize);
        hasMoreTasks.value = loaded.length > taskPageSize;
      } else if (!hasMoreTasks.value && hasNewArrival) {
        // New arrivals can shift offset pages; allow one more historical fetch.
        hasMoreTasks.value = true;
      }
      taskError.value = "";
    }).catch((err: unknown) => {
      if (isCurrent(sessionId, version)) taskError.value = err instanceof ApiError ? err.message : "无法刷新任务状态";
    }).finally(() => {
      if (tasksRequest?.promise === promise) tasksRequest = undefined;
    });
    tasksRequest = { sessionId, version, promise };
    return promise;
  }

  function loadMoreTasks(): Promise<void> {
    const sessionId = activeSessionId.value;
    if (disposed || !sessionId || !hasMoreTasks.value) return Promise.resolve();
    if (moreTasksRequest?.sessionId === sessionId && moreTasksRequest.version === revision) {
      return moreTasksRequest.promise;
    }
    const version = revision;
    const offset = nextTaskOffset;
    const arrivalVersion = firstPageArrivalVersion;
    hasLoadedOlderTasks = true;
    loadingMoreTasks.value = true;
    const promise = api.tasks({ sessionId, limit: taskPageSize + 1, offset }).then((loaded) => {
      if (!isCurrent(sessionId, version)) return;
      allTasks.value = mergeTasks(allTasks.value, loaded.slice(0, taskPageSize));
      nextTaskOffset = offset + Math.min(loaded.length, taskPageSize);
      hasMoreTasks.value = loaded.length > taskPageSize || firstPageArrivalVersion > arrivalVersion;
      taskError.value = "";
    }).catch((err: unknown) => {
      if (isCurrent(sessionId, version)) taskError.value = err instanceof ApiError ? err.message : "无法加载更早任务";
    }).finally(() => {
      if (isCurrent(sessionId, version)) loadingMoreTasks.value = false;
      if (moreTasksRequest?.promise === promise) moreTasksRequest = undefined;
    });
    moreTasksRequest = { sessionId, version, promise };
    return promise;
  }

  function hydrateHistory(history: AgentMessage[]) {
    messages.value = [...history];
    messageArtifacts.value = [];
    pendingApproval.value = null;
    for (const message of history) {
      const metadata = message.metadata;
      if (message.role === "user" && metadata.approval_id === pendingApproval.value?.approval_id) {
        pendingApproval.value = null;
      }
      if (message.role !== "assistant") continue;
      if (Array.isArray(metadata.artifacts)) {
        messageArtifacts.value.push(...(metadata.artifacts as ArtifactEvent[]).map((artifact) => ({
          ...artifact, recorded_at: message.created_at,
        })));
      }
      sources.value = Array.isArray(metadata.sources) ? metadata.sources as SourceEvent[] : [];
      ragBackend.value = String(metadata.rag_backend || "none");
      suggestions.value = Array.isArray(metadata.suggestions) ? metadata.suggestions.map(String) : [];
      events.value = Array.isArray(metadata.events) ? [...metadata.events as ToolEvent[]].reverse().slice(0, 50) : [];
      const approval = asApproval(metadata.approval);
      if (approval) pendingApproval.value = approval;
    }
    onMessagesChanged();
  }

  async function loadHistory(sessionId: string, version: number) {
    const history = await api.sessionHistory(sessionId);
    if (isCurrent(sessionId, version)) hydrateHistory(history);
  }

  function handleStreamEvent(event: StreamEvent) {
    if (event.type === "message_delta") {
      streamingAnswer.value += String(event.delta || "");
      onMessagesChanged();
    } else if (event.type === "suggestions") {
      suggestions.value = Array.isArray(event.items) ? event.items.map(String) : [];
    } else if (event.type === "knowledge_sources") {
      sources.value = (event.sources as SourceEvent[]) || [];
      ragBackend.value = String(event.backend || "none");
    } else if (event.type === "artifact_created") {
      if (event.artifact) messageArtifacts.value.push({
        ...(event.artifact as ArtifactEvent), recorded_at: new Date().toISOString(),
      });
    } else if (event.type === "approval_required") {
      pendingApproval.value = asApproval(event);
    } else if (event.type === "approval_consumed") {
      if (pendingApproval.value?.approval_id === event.approval_id) pendingApproval.value = null;
    } else if (["agent_step", "task_created", "task_update", "tool_call_started", "tool_call_finished", "error"].includes(event.type)) {
      events.value.unshift({
        type: event.type,
        name: String(event.label || event.name || ""),
        tool_call_id: String(event.tool_call_id || ""),
        task_id: String(event.task_id || ""),
        task_type: String(event.task_type || ""),
        status: event.type === "error" ? streamErrorMessage(event) : String(event.status || ""),
      });
      events.value = events.value.slice(0, 50);
      if (event.type === "task_created" || event.type === "task_update") void loadTasks();
      if (event.type === "error") error.value = streamErrorMessage(event);
    }
  }

  async function finishTurn(result: AgentStreamResult, sessionId: string, version: number) {
    if (!isCurrent(sessionId, version)) return;
    error.value = result.status === "failed" ? result.error || "服务器执行失败" : "";
    try {
      await Promise.all([loadHistory(sessionId, version), loadSessions(), loadTasks()]);
      if (isCurrent(sessionId, version)) streamingAnswer.value = "";
    } catch (err) {
      if (isCurrent(sessionId, version)) {
        error.value = err instanceof ApiError ? err.message : "执行已结束，但历史记录刷新失败，请重新打开本会话";
      }
    }
  }

  async function resumeActiveTurn(sessionId: string, version: number, active: ActiveAgentTurn) {
    if (!isCurrent(sessionId, version) || sending.value) return;
    const controller = new AbortController();
    subscription = controller;
    sending.value = true;
    try {
      notice.value = "服务器仍在执行上一条消息，正在恢复进度…";
      const result = await recoverAgentTurn(active.turn_id, (event) => {
        if (isCurrent(sessionId, version)) handleStreamEvent(event);
      }, controller.signal);
      if (isCurrent(sessionId, version)) notice.value = "";
      await finishTurn(result, sessionId, version);
    } catch (err) {
      if (isCurrent(sessionId, version)) {
        notice.value = "";
        error.value = err instanceof ApiError ? err.message : "无法恢复服务器任务，请重新打开本会话";
      }
    } finally {
      if (isCurrent(sessionId, version)) sending.value = false;
    }
  }

  async function selectSession(sessionId: string) {
    drafts.set(activeSessionId.value, input.value);
    detach();
    const version = revision;
    activeSessionId.value = sessionId;
    input.value = drafts.get(sessionId) || "";
    messages.value = [];
    allTasks.value = [];
    hasMoreTasks.value = false;
    loadingMoreTasks.value = false;
    nextTaskOffset = 0;
    hasLoadedOlderTasks = false;
    firstPageArrivalVersion = 0;
    sources.value = [];
    ragBackend.value = "none";
    messageArtifacts.value = [];
    events.value = [];
    streamingAnswer.value = "";
    suggestions.value = [];
    pendingApproval.value = null;
    error.value = "";
    notice.value = "";
    loading.value = true;
    try {
      await Promise.all([loadHistory(sessionId, version), loadTasks()]);
      if (!isCurrent(sessionId, version)) return;
      const active = await activeAgentTurn(sessionId);
      if (!isCurrent(sessionId, version)) return;
      if (active) {
        void resumeActiveTurn(sessionId, version, active);
      } else {
        // The turn can finish after the first history snapshot but before the
        // active lookup. Its final message is committed before the key clears.
        await loadHistory(sessionId, version);
      }
    } catch (err) {
      if (isCurrent(sessionId, version)) error.value = err instanceof ApiError ? err.message : "无法加载对话";
    } finally {
      if (isCurrent(sessionId, version)) loading.value = false;
    }
  }

  async function newSession() {
    if (loading.value) return;
    loading.value = true;
    const version = revision;
    try {
      const session = await api.createSession("新对话");
      if (disposed) return;
      sessions.value.unshift(session);
      if (version === revision) await selectSession(session.id);
    } catch (err) {
      if (!disposed && version === revision) error.value = err instanceof ApiError ? err.message : "无法新建对话";
    } finally {
      if (!disposed && version === revision) loading.value = false;
    }
  }

  async function boot() {
    loading.value = true;
    const version = revision;
    try {
      await loadSessions();
      if (disposed || version !== revision) return;
      const session = sessions.value[0] || await api.createSession("新对话");
      if (disposed || version !== revision) return;
      if (sessions.value.length === 0) sessions.value.push(session);
      await selectSession(session.id);
    } catch (err) {
      if (!disposed && version === revision) {
        error.value = err instanceof ApiError ? err.message : "无法加载智能体";
        loading.value = false;
      }
    }
  }

  async function sendMessage(approval?: Approval) {
    const content = approval ? `确认执行 ${approval.tool_name}` : input.value.trim();
    if (!content || !activeSessionId.value || sending.value || loading.value) return;
    const sessionId = activeSessionId.value;
    const version = revision;
    let turnId: string;
    try { turnId = createClientId(); } catch {
      error.value = "当前浏览器无法创建请求标识，请使用较新版本的浏览器";
      return;
    }
    const localId = `local-${turnId}`;
    const controller = new AbortController();
    subscription = controller;
    sending.value = true;
    if (!approval) input.value = "";
    error.value = "";
    notice.value = "";
    streamingAnswer.value = "";
    suggestions.value = [];
    sources.value = [];
    ragBackend.value = "none";
    events.value = [];
    messages.value.push({ id: localId, role: "user", content, created_at: new Date().toISOString(), metadata: {} });
    onMessagesChanged();
    const onEvent = (event: StreamEvent) => {
      if (isCurrent(sessionId, version)) handleStreamEvent(event);
    };
    const preserveDraft = () => {
      messages.value = messages.value.filter((message) => message.id !== localId);
      if (!approval && !input.value) input.value = content;
    };
    let result: AgentStreamResult;
    try {
      try {
        result = await streamAgentMessage(sessionId, content, onEvent, turnId, { signal: controller.signal, approvalId: approval?.approval_id });
      } catch (err) {
        if (!isCurrent(sessionId, version)) return;
        if (err instanceof ApiError && ![0, 409].includes(err.status)) {
          preserveDraft();
          throw err;
        }
        if (err instanceof ApiError && err.status === 409) {
          preserveDraft();
          notice.value = "上一条消息仍在执行，本次输入已保留，请完成后再发送。";
          const active = await activeAgentTurn(sessionId, controller.signal);
          if (!active) throw err;
          result = await recoverAgentTurn(active.turn_id, onEvent, controller.signal);
        } else {
          notice.value = "连接中断，正在恢复原任务…";
          try {
            // Recover this exact submission even if it already completed. Looking
            // up only the active turn silently loses requests that have just ended.
            result = await recoverAgentTurn(turnId, onEvent, controller.signal);
          } catch (recoveryError) {
            if (isCurrent(sessionId, version) && recoveryError instanceof ApiError && recoveryError.status === 404) {
              preserveDraft();
              throw new ApiError(0, "消息未被服务器接收，输入已保留，请重试");
            }
            throw recoveryError;
          }
          if (isCurrent(sessionId, version)) notice.value = "";
        }
      }
      await finishTurn(result, sessionId, version);
    } catch (err) {
      if (isCurrent(sessionId, version)) {
        notice.value = "";
        error.value = err instanceof ApiError ? err.message : "消息连接中断，服务器可能仍在运行；请重新打开本会话恢复进度";
      }
    } finally {
      if (isCurrent(sessionId, version)) sending.value = false;
    }
  }

  function approvePending() {
    const approval = pendingApproval.value;
    if (approval) return sendMessage(approval);
  }

  function draftTaskFollowUp() {
    const task = followUpTask.value;
    if (!task || sending.value || loading.value) return;
    const prompt = task.status === "failed"
      ? `请根据本会话任务 ${task.id} 的错误信息分析失败原因，并给出排障或安全重试建议。`
      : `请解读本会话已完成任务 ${task.id} 的结果和关联文件，说明关键发现、证据、局限与下一步建议。`;
    input.value = input.value.trim() ? `${input.value.trim()}\n${prompt}` : prompt;
  }

  function dispose() {
    disposed = true;
    detach();
  }

  return {
    sessions, activeSessionId, activeSession, messages, input, loading, sending,
    error, notice, taskError, sources, ragBackend, artifacts, events, tasks, hasMoreTasks, loadingMoreTasks,
    streamingAnswer, suggestions, pendingApproval, followUpTask,
    boot, selectSession, newSession, loadTasks, loadMoreTasks, sendMessage, approvePending, draftTaskFollowUp, dispose,
  };
}
