<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref } from "vue";

import EmptyState from "../components/EmptyState.vue";
import MarkdownMessage from "../components/MarkdownMessage.vue";
import StatusPill from "../components/StatusPill.vue";
import { createAgentConversation } from "../lib/agentConversation";

const messageListRef = ref<HTMLElement | null>(null);
const conversation = createAgentConversation(() => void scrollToBottom());
const {
  sessions, activeSessionId, activeSession, messages, input, loading, sending,
  error, notice, taskError, sources, ragBackend, artifacts, events, tasks,
  streamingAnswer, suggestions, pendingApproval,
  newSession, selectSession, approvePending,
} = conversation;
let taskPollTimer: number | undefined;
let disposed = false;

async function scrollToBottom() {
  await nextTick();
  if (messageListRef.value) {
    messageListRef.value.scrollTop = messageListRef.value.scrollHeight;
  }
}

function sendMessage() {
  return conversation.sendMessage();
}

function useSuggestion(suggestion: string) {
  input.value = suggestion;
}

onMounted(async () => {
  await conversation.boot();
  if (!disposed) taskPollTimer = window.setInterval(() => void conversation.loadTasks(), 3000);
});
onUnmounted(() => {
  disposed = true;
  window.clearInterval(taskPollTimer);
  conversation.dispose();
});
</script>

<template>
  <section class="agent-layout">
    <aside class="session-pane">
      <div class="pane-header">
        <strong>对话记录</strong>
        <button
          class="mini-button"
          :disabled="loading"
          @click="newSession"
        >
          新建
        </button>
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
      <p
        v-if="error"
        class="error-line"
      >
        {{ error }}
      </p>
      <p
        v-if="notice"
        class="muted"
        role="status"
      >
        {{ notice }}
      </p>
      <EmptyState
        v-if="!loading && messages.length === 0"
        title="开始一次分析"
        body="示例：下载 7U5E 并预测 RNA 结合位点。"
      />
      <div
        ref="messageListRef"
        class="message-list"
      >
        <article
          v-for="message in messages"
          :key="message.id"
          class="message-card"
          :class="message.role"
        >
          <span>{{ message.role === "user" ? "用户" : message.role === "assistant" ? "智能体" : message.role }}</span>
          <MarkdownMessage :content="message.content" />
        </article>
        <article
          v-if="streamingAnswer"
          class="message-card assistant streaming"
        >
          <span>智能体生成中</span>
          <MarkdownMessage :content="streamingAnswer" />
        </article>
      </div>
      <div
        v-if="suggestions.length > 0"
        class="suggestion-row"
        aria-label="后续建议"
      >
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
      <div
        v-if="pendingApproval"
        class="rail-card"
        role="region"
        aria-label="待确认操作"
      >
        <strong>{{ pendingApproval.tool_name }} 需要确认</strong>
        <p>{{ pendingApproval.message || "该工具可能消耗较多计算资源，确认后开始执行。" }}</p>
        <button
          class="primary-button"
          :disabled="sending || loading"
          @click="approvePending"
        >
          确认执行
        </button>
      </div>
      <form
        class="composer"
        @submit.prevent="sendMessage"
      >
        <textarea
          v-model="input"
          rows="3"
          placeholder="让 PSKit 搜索结构、调用工具、生成报告，或解释模型依赖..."
          @keydown.meta.enter.prevent="sendMessage"
          @keydown.ctrl.enter.prevent="sendMessage"
        />
        <button
          class="primary-button"
          :disabled="sending || loading || !activeSessionId || !input.trim()"
        >
          发送
        </button>
      </form>
    </div>

    <aside class="agent-rail">
      <div class="rail-card dark">
        <span class="card-label">最新工具事件</span>
        <div
          v-if="events.length === 0"
          class="muted"
        >
          暂无工具事件。
        </div>
        <div
          v-for="(event, index) in events.slice(0, 6)"
          :key="index"
          class="rail-row"
        >
          <strong>{{ event.name || event.task_type || event.type }}</strong>
          <small>{{ event.status || event.tool_call_id || event.task_id }}</small>
        </div>
      </div>

      <div class="rail-card">
        <span class="card-label">结果文件</span>
        <div
          v-if="artifacts.length === 0"
          class="muted"
        >
          工具生成的结果文件会显示在这里。
        </div>
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
        <div
          v-if="sources.length === 0"
          class="muted"
        >
          最近一轮暂无检索来源。
        </div>
        <article
          v-for="source in sources.slice(0, 4)"
          :key="`${source.source}-${source.heading}`"
          class="source-card"
        >
          <strong>{{ source.heading || source.source }}</strong>
          <small>{{ source.source }} · {{ Number(source.score || 0).toFixed(2) }}</small>
        </article>
      </div>

      <div class="rail-card">
        <span class="card-label">最近任务</span>
        <p
          v-if="taskError"
          class="error-line"
        >
          {{ taskError }}
        </p>
        <div
          v-if="tasks.length === 0"
          class="muted"
        >
          暂无任务。
        </div>
        <article
          v-for="task in tasks.slice(0, 4)"
          :key="task.id"
          class="task-mini"
        >
          <strong>{{ task.task_type }}</strong>
          <StatusPill :status="task.status" />
        </article>
      </div>
    </aside>
  </section>
</template>
