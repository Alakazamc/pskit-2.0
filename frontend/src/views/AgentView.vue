<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import EmptyState from "../components/EmptyState.vue";
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
}

async function loadTasks() {
  tasks.value = await api.tasks();
}

async function boot() {
  loading.value = true;
  error.value = "";
  try {
    await loadSessions();
    if (!activeSessionId.value) {
      const session = await api.createSession("New Chat");
      sessions.value.unshift(session);
      activeSessionId.value = session.id;
    }
    await Promise.all([loadHistory(), loadTasks()]);
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : "Could not load agent";
  } finally {
    loading.value = false;
  }
}

async function newSession() {
  const session = await api.createSession("New Chat");
  sessions.value.unshift(session);
  activeSessionId.value = session.id;
  messages.value = [];
  sources.value = [];
  artifacts.value = [];
  events.value = [];
}

async function selectSession(sessionId: string) {
  activeSessionId.value = sessionId;
  sources.value = [];
  artifacts.value = [];
  events.value = [];
  await loadHistory();
}

function handleStreamEvent(event: StreamEvent) {
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

async function sendMessage() {
  const content = input.value.trim();
  if (!content || !activeSessionId.value || sending.value) return;
  sending.value = true;
  input.value = "";
  error.value = "";
  messages.value.push({
    id: `local-${Date.now()}`,
    role: "user",
    content,
    created_at: new Date().toISOString(),
    metadata: {},
  });
  try {
    await streamAgentMessage(activeSessionId.value, content, handleStreamEvent);
    await Promise.all([loadHistory(), loadSessions(), loadTasks()]);
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : "Message failed";
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
        <strong>Conversations</strong>
        <button class="mini-button" @click="newSession">New</button>
      </div>
      <button
        v-for="session in sessions"
        :key="session.id"
        class="session-item"
        :class="{ active: session.id === activeSessionId }"
        @click="selectSession(session.id)"
      >
        <span>{{ session.title || "New Chat" }}</span>
        <small>{{ new Date(session.updated_at).toLocaleString() }}</small>
      </button>
    </aside>

    <div class="chat-panel">
      <div class="chat-header">
        <div>
          <h2>{{ activeSession?.title || "Agent" }}</h2>
          <p>Ask for PDB download, chain splitting, RAG questions, binding prediction, PAIR, or AF3 tasks.</p>
        </div>
        <StatusPill :status="sending ? 'running' : 'ready'" />
      </div>
      <p v-if="error" class="error-line">{{ error }}</p>
      <EmptyState v-if="!loading && messages.length === 0" title="Start an analysis" body="Example: download 7U5E and predict RNA binding sites." />
      <div class="message-list">
        <article v-for="message in messages" :key="message.id" class="message-card" :class="message.role">
          <span>{{ message.role }}</span>
          <p>{{ message.content }}</p>
        </article>
      </div>
      <form class="composer" @submit.prevent="sendMessage">
        <textarea
          v-model="input"
          rows="3"
          placeholder="Ask PSKit to search structures, run tools, create reports, or explain model dependencies..."
          @keydown.meta.enter.prevent="sendMessage"
          @keydown.ctrl.enter.prevent="sendMessage"
        />
        <button class="primary-button" :disabled="sending || !input.trim()">Send</button>
      </form>
    </div>

    <aside class="agent-rail">
      <div class="rail-card dark">
        <span class="card-label">Latest tool events</span>
        <div v-if="events.length === 0" class="muted">No tool event yet.</div>
        <div v-for="event in events.slice(0, 6)" :key="`${event.type}-${event.tool_call_id}-${event.task_id}`" class="rail-row">
          <strong>{{ event.name || event.task_type || event.type }}</strong>
          <small>{{ event.status || event.tool_call_id || event.task_id }}</small>
        </div>
      </div>

      <div class="rail-card">
        <span class="card-label">Artifacts</span>
        <div v-if="artifacts.length === 0" class="muted">Artifacts created by tools will appear here.</div>
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
        <span class="card-label">RAG sources</span>
        <StatusPill :status="ragBackend" />
        <div v-if="sources.length === 0" class="muted">No retrieved source for the latest turn.</div>
        <article v-for="source in sources.slice(0, 4)" :key="`${source.source}-${source.heading}`" class="source-card">
          <strong>{{ source.heading || source.source }}</strong>
          <small>{{ source.source }} · {{ Number(source.score || 0).toFixed(2) }}</small>
        </article>
      </div>

      <div class="rail-card">
        <span class="card-label">Recent tasks</span>
        <div v-if="tasks.length === 0" class="muted">No tasks yet.</div>
        <article v-for="task in tasks.slice(0, 4)" :key="task.id" class="task-mini">
          <strong>{{ task.task_type }}</strong>
          <StatusPill :status="task.status" />
        </article>
      </div>
    </aside>
  </section>
</template>
