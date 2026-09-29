<script setup lang="ts">
import { computed } from "vue";
import { RouterLink, RouterView, useRoute, useRouter } from "vue-router";

import { useAuthStore } from "./stores/auth";

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();

const navItems = computed(() => [
  { to: "/", label: "首页", public: true },
  { to: "/about", label: "技术介绍", public: true },
  { to: "/agent", label: "智能体", public: false },
  { to: "/tasks", label: "任务", public: false },
  { to: "/tools", label: "工具", public: false },
  ...(auth.isAdmin
    ? [
        { to: "/admin/doctor", label: "运行体检", public: false },
        { to: "/admin/users", label: "用户管理", public: false },
      ]
    : []),
]);

const isAuthPage = computed(() => route.name === "login" || route.name === "register");
const roleLabel = computed(() => {
  if (!auth.user) return "";
  return auth.user.role === "admin" ? "管理员" : "用户";
});

async function logout() {
  await auth.logout();
  await router.push({ name: "login" });
}
</script>

<template>
  <div class="app-root">
    <aside
      class="sidebar"
      :class="{ compact: isAuthPage }"
    >
      <RouterLink
        to="/"
        class="brand"
      >
        <span class="brand-mark">P2</span>
        <span>
          <strong>PSKit 2.0</strong>
          <small>蛋白质-RNA AI 工作台</small>
        </span>
      </RouterLink>

      <nav class="nav-list">
        <RouterLink
          v-for="item in navItems"
          :key="item.to"
          :to="item.to"
          class="nav-item"
        >
          {{ item.label }}
        </RouterLink>
      </nav>

      <div class="sidebar-card">
        <span class="card-label">运行时</span>
        <strong>FastAPI · LangGraph · Qdrant</strong>
        <p>工具调用、任务产物和 RAG 来源按账号隔离，保证会话与文件权限安全。</p>
      </div>
    </aside>

    <main class="main-shell">
      <header class="topbar">
        <div>
          <span class="section-label">计算生物智能体</span>
          <h1>{{ route.meta.title || "PSKit 2.0" }}</h1>
        </div>
        <div class="topbar-actions">
          <span
            v-if="auth.user"
            class="user-chip"
          >{{ auth.user.username }} · {{ roleLabel }}</span>
          <button
            v-if="auth.user"
            class="ghost-button"
            @click="logout"
          >
            退出登录
          </button>
          <RouterLink
            v-else
            to="/login"
            class="primary-button"
          >
            登录
          </RouterLink>
        </div>
      </header>

      <RouterView />
    </main>
  </div>
</template>
