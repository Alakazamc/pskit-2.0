<script setup lang="ts">
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import AuthForm from "../components/AuthForm.vue";
import { ApiError } from "../lib/api";
import { useAuthStore } from "../stores/auth";

const auth = useAuthStore();
const route = useRoute();
const router = useRouter();
const error = ref("");
const loading = ref(false);

async function login(username: string, password: string) {
  error.value = "";
  loading.value = true;
  try {
    await auth.login(username, password);
    await router.push(String(route.query.redirect || "/agent"));
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : "Login failed";
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <AuthForm mode="login" :error="error" :loading="loading" @submit="login" />
</template>
