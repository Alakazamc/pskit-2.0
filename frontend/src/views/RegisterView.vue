<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import AuthForm from "../components/AuthForm.vue";
import { ApiError, api } from "../lib/api";
import { useAuthStore } from "../stores/auth";

const auth = useAuthStore();
const router = useRouter();
const error = ref("");
const loading = ref(true);
const bootstrapRequired = ref(false);
const registrationEnabled = ref(false);

onMounted(async () => {
  try {
    const status = await api.registrationStatus();
    bootstrapRequired.value = status.requires_bootstrap_token;
    registrationEnabled.value = status.enabled;
    if (!status.enabled) {
      error.value = "公开注册已关闭，请联系管理员创建账号";
    }
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : "无法读取注册状态";
  } finally {
    loading.value = false;
  }
});

async function register(username: string, password: string, bootstrapToken?: string) {
  if (!registrationEnabled.value) return;
  error.value = "";
  loading.value = true;
  try {
    await auth.register(username, password, bootstrapToken);
    await router.push("/agent");
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : "注册失败";
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <AuthForm
    mode="register"
    :error="error"
    :loading="loading"
    :bootstrap-required="bootstrapRequired"
    :submit-disabled="!registrationEnabled"
    @submit="register"
  />
</template>
