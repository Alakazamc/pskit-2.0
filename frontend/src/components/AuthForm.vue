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
      <h2>{{ props.mode === "login" ? "Sign in" : "Create account" }}</h2>
      <p>
        {{ props.mode === "login" ? "Access protected PSKit tools and reports." : "Create a workspace account for isolated sessions and artifacts." }}
      </p>
    </div>
    <form class="auth-form" @submit.prevent="submit">
      <label>
        Username
        <input v-model="username" name="username" autocomplete="username" minlength="3" required />
      </label>
      <label>
        Password
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
        {{ props.loading ? "Working..." : props.mode === "login" ? "Login" : "Register" }}
      </button>
    </form>
  </section>
</template>
