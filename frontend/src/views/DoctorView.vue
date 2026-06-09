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
    error.value = err instanceof ApiError ? err.message : "Could not load doctor";
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
        <h2>Runtime doctor</h2>
        <p>Admin-only health check for models, APIs, vector database, and scientific binaries.</p>
      </div>
      <button class="ghost-button" @click="loadDoctor">Refresh</button>
    </div>
    <p v-if="error" class="error-line">{{ error }}</p>
    <div v-else-if="report" class="doctor-layout">
      <div class="doctor-summary">
        <StatusPill :status="report.overall" />
        <strong>{{ report.fail_count }} fail · {{ report.warn_count }} warn</strong>
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
