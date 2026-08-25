<script setup lang="ts">
import { onMounted, ref } from "vue";

import StatusPill from "../components/StatusPill.vue";
import { ApiError, api, type AdminMetrics, type AdminUser } from "../lib/api";

const users = ref<AdminUser[]>([]);
const metrics = ref<AdminMetrics | null>(null);
const loading = ref(true);
const error = ref("");
const updating = ref("");

async function load() {
  loading.value = true;
  try {
    [users.value, metrics.value] = await Promise.all([api.adminUsers(), api.adminMetrics()]);
    error.value = "";
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : "无法加载用户管理数据";
  } finally {
    loading.value = false;
  }
}

async function updateUser(user: AdminUser, payload: { disabled?: boolean; role?: string }) {
  updating.value = user.id;
  try {
    await api.updateAdminUser(user.id, payload);
    await load();
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : "无法更新用户";
  } finally {
    updating.value = "";
  }
}

async function revokeSessions(user: AdminUser) {
  updating.value = user.id;
  try {
    await api.revokeUserSessions(user.id);
    await load();
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : "无法撤销会话";
  } finally {
    updating.value = "";
  }
}

onMounted(load);
</script>

<template>
  <section class="content-panel">
    <div class="panel-header">
      <div>
        <h2>用户与运行指标</h2>
        <p>停用账号会立即撤销该用户的所有登录会话，管理操作写入审计日志。</p>
      </div>
      <button
        class="ghost-button"
        :disabled="loading"
        @click="load"
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
    <div
      v-if="metrics"
      class="metric-grid"
    >
      <article><span>用户</span><strong>{{ metrics.users }}</strong></article>
      <article><span>活跃会话</span><strong>{{ metrics.active_sessions }}</strong></article>
      <article><span>运行中 Agent</span><strong>{{ metrics.active_agent_turns }}</strong></article>
      <article><span>结果文件</span><strong>{{ metrics.artifacts }}</strong></article>
    </div>
    <div class="admin-user-list">
      <article
        v-for="user in users"
        :key="user.id"
        class="admin-user-row"
      >
        <div>
          <strong>{{ user.username }}</strong>
          <small>{{ user.id }} · {{ new Date(user.created_at).toLocaleString() }}</small>
        </div>
        <StatusPill :status="user.disabled ? 'disabled' : user.role" />
        <span>{{ user.active_sessions }} 个会话</span>
        <div class="admin-actions">
          <button
            class="mini-button"
            :disabled="updating === user.id"
            @click="updateUser(user, { disabled: !user.disabled })"
          >
            {{ user.disabled ? "启用" : "停用" }}
          </button>
          <button
            class="mini-button"
            :disabled="updating === user.id"
            @click="updateUser(user, { role: user.role === 'admin' ? 'user' : 'admin' })"
          >
            {{ user.role === "admin" ? "降为用户" : "设为管理员" }}
          </button>
          <button
            class="mini-button"
            :disabled="updating === user.id || user.active_sessions === 0"
            @click="revokeSessions(user)"
          >
            撤销会话
          </button>
        </div>
      </article>
    </div>
  </section>
</template>
