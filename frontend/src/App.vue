<script setup lang="ts">
import { computed } from "vue";
import { RouterLink, RouterView, useRoute, useRouter } from "vue-router";

import { useAuthStore } from "./stores/auth";

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();

const navItems = computed(() => [
  { to: "/", label: "Home", public: true },
  { to: "/about", label: "About", public: true },
  { to: "/agent", label: "Agent", public: false },
  { to: "/tasks", label: "Tasks", public: false },
  { to: "/tools", label: "Tools", public: false },
  ...(auth.isAdmin ? [{ to: "/admin/doctor", label: "Doctor", public: false }] : []),
]);

const isAuthPage = computed(() => route.name === "login" || route.name === "register");

async function logout() {
  await auth.logout();
  await router.push({ name: "login" });
}
</script>

<template>
  <div class="app-root">
    <aside class="sidebar" :class="{ compact: isAuthPage }">
      <RouterLink to="/" class="brand">
        <span class="brand-mark">P2</span>
        <span>
          <strong>PSKit 2.0</strong>
          <small>Protein-RNA AI workbench</small>
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
        <span class="card-label">Runtime</span>
        <strong>FastAPI · LangGraph · Qdrant</strong>
        <p>Authenticated tools, task artifacts, and RAG sources are isolated per user.</p>
      </div>
    </aside>

    <main class="main-shell">
      <header class="topbar">
        <div>
          <span class="section-label">Computational Biology Agent</span>
          <h1>{{ route.meta.title || "PSKit 2.0" }}</h1>
        </div>
        <div class="topbar-actions">
          <span v-if="auth.user" class="user-chip">{{ auth.user.username }} · {{ auth.user.role }}</span>
          <button v-if="auth.user" class="ghost-button" @click="logout">Logout</button>
          <RouterLink v-else to="/login" class="primary-button">Login</RouterLink>
        </div>
      </header>

      <RouterView />
    </main>
  </div>
</template>
