<script setup lang="ts">
import { onMounted, ref } from "vue";

import EmptyState from "../components/EmptyState.vue";
import StatusPill from "../components/StatusPill.vue";
import { ApiError, api, type Task } from "../lib/api";

const tasks = ref<Task[]>([]);
const loading = ref(true);
const error = ref("");

async function loadTasks() {
  loading.value = true;
  error.value = "";
  try {
    tasks.value = await api.tasks();
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : "无法加载任务";
  } finally {
    loading.value = false;
  }
}

onMounted(loadTasks);
</script>

<template>
  <section class="content-panel">
    <div class="panel-header">
      <div>
        <h2>任务</h2>
        <p>长时间运行的模型任务、进度状态和生成结果文件。</p>
      </div>
      <button class="ghost-button" @click="loadTasks">刷新</button>
    </div>
    <p v-if="error" class="error-line">{{ error }}</p>
    <EmptyState v-else-if="!loading && tasks.length === 0" title="暂无任务" body="在智能体中调用模型工具后，会在这里生成排队任务。" />
    <div v-else class="task-list">
      <article v-for="task in tasks" :key="task.id" class="task-row">
        <div>
          <strong>{{ task.task_type }}</strong>
          <small>{{ task.id }}</small>
        </div>
        <div class="task-progress">
          <span>{{ Math.round(task.progress * 100) }}%</span>
          <div class="progress-track">
            <div class="progress-fill" :style="{ width: `${Math.round(task.progress * 100)}%` }"></div>
          </div>
        </div>
        <StatusPill :status="task.status" />
        <p v-if="task.error_message" class="task-error">{{ task.error_type }} · {{ task.error_message }}</p>
      </article>
    </div>
  </section>
</template>
