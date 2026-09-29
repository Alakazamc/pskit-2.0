<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";

import EmptyState from "../components/EmptyState.vue";
import StatusPill from "../components/StatusPill.vue";
import { ApiError, api, type Task } from "../lib/api";

const tasks = ref<Task[]>([]);
const loading = ref(true);
const error = ref("");
const retryingTaskId = ref("");
const page = ref(0);
const pageSize = 20;
const hasNextPage = ref(false);
let pollTimer: number | undefined;
let requestVersion = 0;
let pendingPage: number | undefined;
let disposed = false;

async function loadTasks(silent = false) {
  const requestedPage = page.value;
  if (disposed || (silent && pendingPage === requestedPage)) return;
  const version = ++requestVersion;
  pendingPage = requestedPage;
  if (!silent) loading.value = true;
  try {
    const loaded = await api.tasks({ limit: pageSize + 1, offset: requestedPage * pageSize });
    if (disposed || version !== requestVersion) return;
    tasks.value = loaded.slice(0, pageSize);
    hasNextPage.value = loaded.length > pageSize;
    error.value = "";
  } catch (err) {
    if (!disposed && version === requestVersion) error.value = err instanceof ApiError ? err.message : "无法加载任务";
  } finally {
    if (!disposed && version === requestVersion) {
      pendingPage = undefined;
      loading.value = false;
    }
  }
}

async function changePage(delta: number) {
  if (loading.value || page.value + delta < 0) return;
  page.value += delta;
  tasks.value = [];
  await loadTasks();
}

async function retryTask(task: Task) {
  retryingTaskId.value = task.id;
  error.value = "";
  try {
    await api.retryTask(task.id);
    await loadTasks(true);
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : "无法重试任务";
  } finally {
    retryingTaskId.value = "";
  }
}

function formatBytes(size: number) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

onMounted(async () => {
  await loadTasks();
  if (!disposed) pollTimer = window.setInterval(() => void loadTasks(true), 3000);
});
onUnmounted(() => {
  disposed = true;
  window.clearInterval(pollTimer);
});
</script>

<template>
  <section class="content-panel">
    <div class="panel-header">
      <div>
        <h2>任务</h2>
        <p>长时间运行的模型任务、进度状态和生成结果文件。</p>
      </div>
      <button
        class="ghost-button"
        :disabled="loading"
        @click="loadTasks()"
      >
        刷新
      </button>
    </div>
    <p
      v-if="error"
      class="error-line"
    >
      {{ error }}
    </p>
    <EmptyState
      v-else-if="!loading && tasks.length === 0"
      :title="page === 0 ? '暂无任务' : '本页暂无任务'"
      :body="page === 0 ? '在智能体中调用模型工具后，会在这里生成排队任务。' : '可以返回上一页查看较新的任务。'"
    />
    <div
      v-else
      class="task-list"
    >
      <article
        v-for="task in tasks"
        :key="task.id"
        class="task-row"
      >
        <div>
          <strong>{{ task.task_type }}</strong>
          <small>{{ task.id }}</small>
        </div>
        <div class="task-progress">
          <span>{{ Math.round(task.progress * 100) }}%</span>
          <div class="progress-track">
            <div
              class="progress-fill"
              :style="{ width: `${Math.round(task.progress * 100)}%` }"
            />
          </div>
        </div>
        <StatusPill :status="task.status" />
        <div
          v-if="task.artifacts.length > 0"
          class="task-artifacts"
        >
          <a
            v-for="artifact in task.artifacts"
            :key="artifact.id"
            class="artifact-link"
            :href="artifact.download_url"
            target="_blank"
            rel="noreferrer"
          >
            {{ artifact.filename }} · {{ formatBytes(artifact.size_bytes) }}
          </a>
        </div>
        <p
          v-if="task.error_message"
          class="task-error"
        >
          {{ task.error_type }} · {{ task.error_message }}
        </p>
        <button
          v-if="task.status === 'failed' && !task.retry_task_id"
          class="mini-button"
          :disabled="retryingTaskId === task.id"
          @click="retryTask(task)"
        >
          {{ retryingTaskId === task.id ? "提交中" : "重试" }}
        </button>
        <small v-else-if="task.retry_task_id">已创建重试任务 {{ task.retry_task_id }}</small>
      </article>
    </div>
    <nav
      class="panel-header"
      aria-label="任务分页"
    >
      <button
        class="ghost-button"
        :disabled="loading || page === 0"
        @click="changePage(-1)"
      >
        上一页
      </button>
      <span aria-live="polite">第 {{ page + 1 }} 页 · 每页 {{ pageSize }} 条</span>
      <button
        class="ghost-button"
        :disabled="loading || !hasNextPage"
        @click="changePage(1)"
      >
        下一页
      </button>
    </nav>
  </section>
</template>
