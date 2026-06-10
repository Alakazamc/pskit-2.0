<script setup lang="ts">
import { onMounted, ref } from "vue";

import StatusPill from "../components/StatusPill.vue";
import { ApiError, api, type DoctorReport } from "../lib/api";

const report = ref<DoctorReport | null>(null);
const loading = ref(true);
const error = ref("");

async function loadDoctor() {
  loading.value = true;
  error.value = "";
  try {
    report.value = await api.doctor();
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : "无法加载运行体检";
  } finally {
    loading.value = false;
  }
}

onMounted(loadDoctor);
</script>

<template>
  <section class="content-panel">
    <div class="panel-header">
      <div>
        <h2>运行体检</h2>
        <p>管理员可见，用于检查模型权重、API、向量数据库和科学计算二进制工具。</p>
      </div>
      <button class="ghost-button" @click="loadDoctor">刷新</button>
    </div>
    <p v-if="error" class="error-line">{{ error }}</p>
    <div v-else-if="report" class="doctor-layout">
      <div class="doctor-summary">
        <StatusPill :status="report.overall" />
        <strong>{{ report.fail_count }} 个失败 · {{ report.warn_count }} 个警告</strong>
      </div>
      <div class="check-list">
        <article v-for="check in report.checks" :key="check.name" class="check-row">
          <div>
            <strong>{{ check.name }}</strong>
            <small>{{ check.detail }}</small>
          </div>
          <StatusPill :status="check.status" />
        </article>
      </div>
    </div>
  </section>
</template>
