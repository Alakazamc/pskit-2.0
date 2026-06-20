<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from "vue";

import EmptyState from "../components/EmptyState.vue";
import MarkdownMessage from "../components/MarkdownMessage.vue";
import StatusPill from "../components/StatusPill.vue";
import {
  ApiError,
  api,
  streamAgentMessage,
  type AgentMessage,
  type AgentSession,
  type StreamEvent,
  type Task,
} from "../lib/api";

type ArtifactEvent = {
  artifact_id: string;
  filename?: string;
  download_url?: string;
};

type SourceEvent = {
  source?: string;
  heading?: string;
  score?: number;
  content?: string;
};

type ToolEvent = {
  type: string;
  name?: string;
  tool_call_id?: string;
  task_id?: string;
  task_type?: string;
  status?: string;
};

const sessions = ref<AgentSession[]>([]);
const activeSessionId = ref("");
const messages = ref<AgentMessage[]>([]);
const input = ref("");
const loading = ref(true);
const sending = ref(false);
const error = ref("");
const sources = ref<SourceEvent[]>([]);
const ragBackend = ref("none");
const artifacts = ref<ArtifactEvent[]>([]);
const events = ref<ToolEvent[]>([]);
const tasks = ref<Task[]>([]);
const streamingAnswer = ref("");
const suggestions = ref<string[]>([]);
const messageListRef = ref<HTMLElement | null>(null);

const activeSession = computed(() => sessions.value.find((item) => item.id === activeSessionId.value));

async function loadSessions() {
  sessions.value = await api.sessions();
  if (!activeSessionId.value && sessions.value.length > 0) {
    activeSessionId.value = sessions.value[0]?.id || "";
  }
}

async function loadHistory() {
  if (!activeSessionId.value) return;
  messages.value = await api.sessionHistory(activeSessionId.value);
  await scrollToBottom();
}

async function loadTasks() {
  tasks.value = await api.tasks();
}

async function scrollToBottom() {
  await nextTick();
  if (messageListRef.value) {
    messageListRef.value.scrollTop = messageListRef.value.scrollHeight;
  }
}

async function boot() {
  loading.value = true;
  error.value = "";
  try {
    await loadSessions();
    if (!activeSessionId.value) {
      const session = await api.createSession("新对话");
      sessions.value.unshift(session);
      activeSessionId.value = session.id;
    }
    await Promise.all([loadHistory(), loadTasks()]);
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : "无法加载智能体";
  } finally {
    loading.value = false;
  }
}

async function newSession() {
  const session = await api.createSession("新对话");
  sessions.value.unshift(session);
  activeSessionId.value = session.id;
  messages.value = [];
  sources.value = [];
  artifacts.value = [];
  events.value = [];
  streamingAnswer.value = "";
  suggestions.value = [];
}

async function selectSession(sessionId: string) {
  activeSessionId.value = sessionId;
  sources.value = [];
  artifacts.value = [];
  events.value = [];
  streamingAnswer.value = "";
  suggestions.value = [];
  await loadHistory();
}

function handleStreamEvent(event: StreamEvent) {
  if (event.type === "agent_step") {
    events.value.unshift({
      type: "agent_step",
      name: String(event.label || "执行步骤"),
      status: String(event.status || ""),
    });
    return;
  }
  if (event.type === "message_delta") {
    streamingAnswer.value += String(event.delta || "");
    void scrollToBottom();
    return;
  }
  if (event.type === "suggestions") {
    suggestions.value = Array.isArray(event.items) ? event.items.map((item) => String(item)) : [];
    return;
  }
  if (event.type === "knowledge_sources") {
    sources.value = (event.sources as SourceEvent[]) || [];
    ragBackend.value = String(event.backend || "unknown");
    return;
  }
  if (event.type === "artifact_created") {
    const artifact = event.artifact as ArtifactEvent | undefined;
    if (artifact) artifacts.value.unshift(artifact);
    return;
  }
  if (event.type === "task_created") {
    events.value.unshift({
      type: "task_created",
      task_id: String(event.task_id || ""),
      task_type: String(event.task_type || ""),
      status: String(event.status || ""),
    });
    void loadTasks();
    return;
  }
  if (event.type === "tool_call_started" || event.type === "tool_call_finished") {
    events.value.unshift({
      type: event.type,
      name: String(event.name || ""),
      tool_call_id: String(event.tool_call_id || ""),
    });
    return;
  }
  if (event.type === "error") {
    events.value.unshift({ type: "error", status: JSON.stringify(event.error || event.message || event) });
  }
}

function useSuggestion(suggestion: string) {
  input.value = suggestion;
}

async function sendMessage() {
  const content = input.value.trim();
  if (!content || !activeSessionId.value || sending.value) return;
  sending.value = true;
  input.value = "";
  error.value = "";
  streamingAnswer.value = "";
  suggestions.value = [];
  messages.value.push({
    id: `local-${Date.now()}`,
    role: "user",
    content,
    created_at: new Date().toISOString(),
    metadata: {},
  });
  void scrollToBottom();
  try {
    await streamAgentMessage(activeSessionId.value, content, handleStreamEvent);
    await Promise.all([loadHistory(), loadSessions(), loadTasks()]);
    streamingAnswer.value = "";
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : "消息发送失败";
  } finally {
    sending.value = false;
  }
}

onMounted(boot);
</script>

<template>
  <section class="agent-layout">
    <aside class="session-pane">
      <div class="pane-header">
        <strong>对话记录</strong>
        <button class="mini-button" @click="newSession">新建</button>
      </div>
      <button
        v-for="session in sessions"
        :key="session.id"
        class="session-item"
        :class="{ active: session.id === activeSessionId }"
        @click="selectSession(session.id)"
      >
        <span>{{ session.title || "新对话" }}</span>
        <small>{{ new Date(session.updated_at).toLocaleString() }}</small>
      </button>
    </aside>

    <div class="chat-panel">
      <div class="chat-header">
        <div>
          <h2>{{ activeSession?.title || "智能体" }}</h2>
          <p>可以请求 PDB 下载、链拆分、RAG 问答、结合位点预测、PAIR 或 AF3 任务。</p>
        </div>
        <StatusPill :status="sending ? 'running' : 'ready'" />
      </div>
      <p v-if="error" class="error-line">{{ error }}</p>
      <EmptyState v-if="!loading && messages.length === 0" title="开始一次分析" body="示例：下载 7U5E 并预测 RNA 结合位点。" />
      <div ref="messageListRef" class="message-list">
        <article v-for="message in messages" :key="message.id" class="message-card" :class="message.role">
          <span>{{ message.role === "user" ? "用户" : message.role === "assistant" ? "智能体" : message.role }}</span>
          <MarkdownMessage :content="message.content" />
        </article>
        <article v-if="streamingAnswer" class="message-card assistant streaming">
          <span>智能体生成中</span>
          <MarkdownMessage :content="streamingAnswer" />
        </article>
      </div>
      <div v-if="suggestions.length > 0" class="suggestion-row" aria-label="后续建议">
        <button
          v-for="suggestion in suggestions"
          :key="suggestion"
          class="suggestion-chip"
          type="button"
          @click="useSuggestion(suggestion)"
        >
          {{ suggestion }}
        </button>
      </div>
      <form class="composer" @submit.prevent="sendMessage">
        <textarea
          v-model="input"
          rows="3"
          placeholder="让 PSKit 搜索结构、调用工具、生成报告，或解释模型依赖..."
          @keydown.meta.enter.prevent="sendMessage"
          @keydown.ctrl.enter.prevent="sendMessage"
        />
        <button class="primary-button" :disabled="sending || !input.trim()">发送</button>
      </form>
    </div>

    <aside class="agent-rail">
      <div class="rail-card dark">
        <span class="card-label">最新工具事件</span>
        <div v-if="events.length === 0" class="muted">暂无工具事件。</div>
        <div v-for="event in events.slice(0, 6)" :key="`${event.type}-${event.tool_call_id}-${event.task_id}`" class="rail-row">
          <strong>{{ event.name || event.task_type || event.type }}</strong>
          <small>{{ event.status || event.tool_call_id || event.task_id }}</small>
        </div>
      </div>

      <div class="rail-card">
        <span class="card-label">结果文件</span>
        <div v-if="artifacts.length === 0" class="muted">工具生成的结果文件会显示在这里。</div>
        <a
          v-for="artifact in artifacts.slice(0, 6)"
          :key="artifact.artifact_id"
          class="artifact-link"
          :href="artifact.download_url"
          target="_blank"
          rel="noreferrer"
        >
          {{ artifact.filename || artifact.artifact_id }}
        </a>
      </div>

      <div class="rail-card">
        <span class="card-label">RAG 来源</span>
        <StatusPill :status="ragBackend" />
        <div v-if="sources.length === 0" class="muted">最近一轮暂无检索来源。</div>
        <article v-for="source in sources.slice(0, 4)" :key="`${source.source}-${source.heading}`" class="source-card">
          <strong>{{ source.heading || source.source }}</strong>
          <small>{{ source.source }} · {{ Number(source.score || 0).toFixed(2) }}</small>
        </article>
      </div>

      <div class="rail-card">
        <span class="card-label">最近任务</span>
        <div v-if="tasks.length === 0" class="muted">暂无任务。</div>
        <article v-for="task in tasks.slice(0, 4)" :key="task.id" class="task-mini">
          <strong>{{ task.task_type }}</strong>
          <StatusPill :status="task.status" />
        </article>
      </div>
    </aside>
  </section>
</template>
