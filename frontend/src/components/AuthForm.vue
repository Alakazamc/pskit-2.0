<script setup lang="ts">
import { ref } from "vue";

const props = defineProps<{
  mode: "login" | "register";
  error?: string;
  loading?: boolean;
}>();

const emit = defineEmits<{
  submit: [username: string, password: string];
}>();

const username = ref("");
const password = ref("");

function submit() {
  emit("submit", username.value.trim(), password.value);
}
</script>

<template>
  <section class="auth-panel">
    <div class="auth-copy">
      <h2>{{ props.mode === "login" ? "登录账号" : "注册账号" }}</h2>
      <p>
        {{ props.mode === "login" ? "登录后可以使用受保护的 PSKit 工具、任务和报告。" : "创建账号后，对话、任务和结果文件会按用户隔离保存。" }}
      </p>
    </div>
    <form class="auth-form" @submit.prevent="submit">
      <label>
        用户名
        <input v-model="username" name="username" autocomplete="username" minlength="3" required />
      </label>
      <label>
        密码
        <input
          v-model="password"
          name="password"
          type="password"
          autocomplete="current-password"
          minlength="8"
          required
        />
      </label>
      <p v-if="props.error" class="error-line">{{ props.error }}</p>
      <button class="primary-button full" :disabled="props.loading">
        {{ props.loading ? "处理中..." : props.mode === "login" ? "登录" : "注册" }}
      </button>
      <p class="auth-switch">
        <span>{{ props.mode === "login" ? "还没有账号？" : "已经有账号？" }}</span>
        <RouterLink :to="props.mode === 'login' ? '/register' : '/login'">
          {{ props.mode === "login" ? "去注册" : "去登录" }}
        </RouterLink>
      </p>
    </form>
  </section>
</template>
