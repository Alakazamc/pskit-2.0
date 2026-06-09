<script setup lang="ts">
defineProps<{
  title: string;
  body: string;
}>();
</script>

<template>
  <div class="empty-state">
    <div class="empty-orbit"></div>
    <h3>{{ title }}</h3>
    <p>{{ body }}</p>
  </div>
</template>
