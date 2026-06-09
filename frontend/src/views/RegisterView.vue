<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";

import AuthForm from "../components/AuthForm.vue";
import { ApiError } from "../lib/api";
import { useAuthStore } from "../stores/auth";

const auth = useAuthStore();
const router = useRouter();
const error = ref("");
const loading = ref(false);

async function register(username: string, password: string) {
  error.value = "";
  loading.value = true;
  try {
    await auth.register(username, password);
    await router.push("/agent");
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : "Registration failed";
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <AuthForm mode="register" :error="error" :loading="loading" @submit="register" />
</template>
