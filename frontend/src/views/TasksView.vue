<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";

import EmptyState from "../components/EmptyState.vue";
import StatusPill from "../components/StatusPill.vue";
import { ApiError, api, type Task } from "../lib/api";

const tasks = ref<Task[]>([]);
const loading = ref(true);
const error = ref("");
const retryingTaskId = ref("");
let pollTimer: number | undefined;

async function loadTasks(silent = false) {
  if (!silent) loading.value = true;
  try {
    tasks.value = await api.tasks();
    error.value = "";
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : "无法加载任务";
  } finally {
    if (!silent) loading.value = false;
  }
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
  pollTimer = window.setInterval(() => void loadTasks(true), 3000);
});
onUnmounted(() => window.clearInterval(pollTimer));
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
      title="暂无任务"
      body="在智能体中调用模型工具后，会在这里生成排队任务。"
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
  </section>
</template>
