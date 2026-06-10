<script setup lang="ts">
import { onMounted, ref } from "vue";

import EmptyState from "../components/EmptyState.vue";
import StatusPill from "../components/StatusPill.vue";
import { ApiError, api, type ToolSpec } from "../lib/api";

const tools = ref<ToolSpec[]>([]);
const loading = ref(true);
const error = ref("");

onMounted(async () => {
  try {
    tools.value = (await api.tools()).tools;
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : "无法加载工具";
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <section class="content-panel">
    <div class="panel-header">
      <div>
        <h2>工具目录</h2>
        <p>PSKit 智能体可调用的结构化工具列表。</p>
      </div>
      <StatusPill status="protected" />
    </div>
    <p v-if="error" class="error-line">{{ error }}</p>
    <EmptyState v-else-if="!loading && tools.length === 0" title="暂无工具" body="后端返回的工具目录为空。" />
    <div v-else class="tool-grid">
      <article v-for="tool in tools" :key="tool.name" class="tool-card">
        <div class="tool-card-head">
          <h3>{{ tool.name }}</h3>
          <StatusPill :status="tool.long_running ? 'queued' : 'sync'" />
        </div>
        <p>{{ tool.description }}</p>
      </article>
    </div>
  </section>
</template>
