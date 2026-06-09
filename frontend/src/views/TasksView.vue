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
    error.value = err instanceof ApiError ? err.message : "Could not load tasks";
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
        <h2>Tasks</h2>
        <p>Long-running model jobs and generated artifacts.</p>
      </div>
      <button class="ghost-button" @click="loadTasks">Refresh</button>
    </div>
    <p v-if="error" class="error-line">{{ error }}</p>
    <EmptyState v-else-if="!loading && tasks.length === 0" title="No tasks yet" body="Submit a model tool from the Agent to create a queued task." />
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
