<script setup lang="ts">
const props = defineProps<{
  status: string;
}>();

const statusLabels: Record<string, string> = {
  ok: "正常",
  ready: "就绪",
  sync: "同步",
  succeeded: "成功",
  protected: "受保护",
  qdrant: "Qdrant",
  warn: "警告",
  queued: "排队中",
  running: "运行中",
  keyword_fallback: "关键词兜底",
  none: "无",
  unknown: "未知",
  fail: "失败",
  failed: "失败",
  error: "错误",
};
</script>

<template>
  <span class="status-pill" :class="`status-${status}`">{{ statusLabels[props.status] || props.status }}</span>
</template>
