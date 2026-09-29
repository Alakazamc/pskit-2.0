import { computed, ref } from "vue";

import {
  ApiError, activeAgentTurn, api, createClientId, recoverAgentTurn,
  streamAgentMessage, streamErrorMessage,
  type AgentMessage, type AgentSession, type AgentStreamResult, type StreamEvent, type Task,
} from "./api";

type ArtifactEvent = { artifact_id: string; filename?: string; download_url?: string };
type SourceEvent = { source?: string; heading?: string; score?: number; content?: string };
type Approval = { approval_id: string; tool_name: string; message?: string; argument_keys?: string[] };
type ToolEvent = { type: string; name?: string; tool_call_id?: string; task_id?: string; task_type?: string; status?: string };

function asApproval(value: unknown): Approval | null {
  if (!value || typeof value !== "object") return null;
  const item = value as Partial<Approval>;
  return typeof item.approval_id === "string" && /^[0-9a-f]{64}$/.test(item.approval_id)
    && typeof item.tool_name === "string" ? item as Approval : null;
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
  const streamingAnswer = ref("");
  const suggestions = ref<string[]>([]);
  const pendingApproval = ref<Approval | null>(null);
  const activeSession = computed(() => sessions.value.find((item) => item.id === activeSessionId.value));
  const tasks = computed(() => allTasks.value.filter((task) => task.session_id === activeSessionId.value));
  const artifacts = computed(() => [...new Map([
    ...messageArtifacts.value,
    ...tasks.value.flatMap((task) => task.artifacts.map((artifact) => ({
      artifact_id: artifact.id, filename: artifact.filename, download_url: artifact.download_url,
    }))),
  ].map((artifact) => [artifact.artifact_id, artifact])).values()]);
  const drafts = new Map<string, string>();
  let revision = 0;
  let disposed = false;
  let subscription: AbortController | undefined;
  let tasksRequest: Promise<void> | undefined;

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
    if (disposed) return Promise.resolve();
    // Polling and stream events share a single request so slow responses cannot
    // overwrite newer task state or accumulate an unbounded request backlog.
    tasksRequest ??= api.tasks().then((loaded) => {
      if (disposed) return;
      allTasks.value = loaded;
      taskError.value = "";
    }).catch((err: unknown) => {
      if (!disposed) taskError.value = err instanceof ApiError ? err.message : "无法刷新任务状态";
    }).finally(() => { tasksRequest = undefined; });
    return tasksRequest;
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
      if (Array.isArray(metadata.artifacts)) messageArtifacts.value.push(...metadata.artifacts as ArtifactEvent[]);
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
      if (event.artifact) messageArtifacts.value.push(event.artifact as ArtifactEvent);
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

  async function resumeActiveTurn(sessionId: string, version: number) {
    if (!isCurrent(sessionId, version) || sending.value) return;
    const controller = new AbortController();
    subscription = controller;
    sending.value = true;
    try {
      const active = await activeAgentTurn(sessionId, controller.signal);
      if (!active || !isCurrent(sessionId, version)) return;
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
      if (isCurrent(sessionId, version)) void resumeActiveTurn(sessionId, version);
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

  function dispose() {
    disposed = true;
    detach();
  }

  return {
    sessions, activeSessionId, activeSession, messages, input, loading, sending,
    error, notice, taskError, sources, ragBackend, artifacts, events, tasks,
    streamingAnswer, suggestions, pendingApproval,
    boot, selectSession, newSession, loadTasks, sendMessage, approvePending, dispose,
  };
}
