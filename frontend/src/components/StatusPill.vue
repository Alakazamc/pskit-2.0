<script setup lang="ts">
defineProps<{
  status: string;
}>();
</script>

<template>
  <span class="status-pill" :class="`status-${status}`">{{ status }}</span>
</template>
