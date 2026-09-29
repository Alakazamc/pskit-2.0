<script setup lang="ts">
import { ref } from "vue";

const props = defineProps<{
  mode: "login" | "register";
  error?: string;
  loading?: boolean;
  bootstrapRequired?: boolean;
  submitDisabled?: boolean;
}>();

const emit = defineEmits<{
  submit: [username: string, password: string, bootstrapToken?: string];
}>();

const username = ref("");
const password = ref("");
const bootstrapToken = ref("");

function submit() {
  emit(
    "submit",
    username.value.trim(),
    password.value,
    bootstrapToken.value.trim() || undefined,
  );
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
    <form
      class="auth-form"
      @submit.prevent="submit"
    >
      <label>
        用户名
        <input
          v-model="username"
          name="username"
          autocomplete="username"
          minlength="3"
          required
        >
      </label>
      <label>
        密码
        <input
          v-model="password"
          name="password"
          type="password"
          :autocomplete="props.mode === 'register' ? 'new-password' : 'current-password'"
          minlength="8"
          required
        >
      </label>
      <label v-if="props.mode === 'register' && props.bootstrapRequired">
        首位管理员引导令牌
        <input
          v-model="bootstrapToken"
          name="bootstrap-token"
          type="password"
          autocomplete="off"
          minlength="24"
          required
        >
        <small>请输入服务器管理员配置的 INITIAL_ADMIN_BOOTSTRAP_TOKEN。</small>
      </label>
      <p
        v-if="props.error"
        class="error-line"
      >
        {{ props.error }}
      </p>
      <button
        class="primary-button full"
        :disabled="props.loading || props.submitDisabled"
      >
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
