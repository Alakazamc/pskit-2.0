<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{
  content: string;
}>();

function escapeHtml(value: string) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function safeUrl(value: string) {
  try {
    const parsed = new URL(value, window.location.origin);
    if (["http:", "https:", "mailto:"].includes(parsed.protocol)) {
      return escapeHtml(value);
    }
  } catch {
    return "";
  }
  return "";
}

function renderInline(value: string) {
  let html = escapeHtml(value);
  html = html.replace(/`([^`]+)`/g, (_match, code: string) => `<code>${code}</code>`);
  html = html.replace(/\[([^\]]+)]\(([^)\s]+)\)/g, (_match, label: string, url: string) => {
    const href = safeUrl(url);
    return href ? `<a href="${href}" target="_blank" rel="noreferrer">${label}</a>` : label;
  });
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>");
  return html;
}

function renderMarkdown(value: string) {
  const lines = value.replace(/\r\n/g, "\n").split("\n");
  const blocks: string[] = [];
  let paragraph: string[] = [];
  let listItems: string[] = [];
  let inCode = false;
  let codeLang = "";
  let codeLines: string[] = [];

  function flushParagraph() {
    if (!paragraph.length) return;
    blocks.push(`<p>${paragraph.map(renderInline).join("<br>")}</p>`);
    paragraph = [];
  }

  function flushList() {
    if (!listItems.length) return;
    blocks.push(`<ul>${listItems.map((item) => `<li>${renderInline(item)}</li>`).join("")}</ul>`);
    listItems = [];
  }

  function flushCode() {
    const lang = codeLang ? ` data-lang="${escapeHtml(codeLang)}"` : "";
    blocks.push(`<pre${lang}><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
    codeLines = [];
    codeLang = "";
  }

  for (const line of lines) {
    const trimmed = line.trim();
    const fence = trimmed.match(/^```([A-Za-z0-9_-]+)?\s*$/);
    if (fence) {
      if (inCode) {
        flushCode();
        inCode = false;
      } else {
        flushParagraph();
        flushList();
        inCode = true;
        codeLang = fence[1] || "";
      }
      continue;
    }

    if (inCode) {
      codeLines.push(line);
      continue;
    }

    if (!trimmed) {
      flushParagraph();
      flushList();
      continue;
    }

    const heading = trimmed.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      flushList();
      const hashes = heading[1] || "#";
      const title = heading[2] || "";
      const level = hashes.length + 2;
      blocks.push(`<h${level}>${renderInline(title)}</h${level}>`);
      continue;
    }

    const list = trimmed.match(/^[-*]\s+(.+)$/) || trimmed.match(/^\d+[.)]\s+(.+)$/);
    if (list) {
      flushParagraph();
      listItems.push(list[1] || "");
      continue;
    }

    const quote = trimmed.match(/^>\s?(.+)$/);
    if (quote) {
      flushParagraph();
      flushList();
      blocks.push(`<blockquote>${renderInline(quote[1] || "")}</blockquote>`);
      continue;
    }

    flushList();
    paragraph.push(line);
  }

  flushParagraph();
  flushList();
  if (inCode) flushCode();
  return blocks.join("");
}

const rendered = computed(() => renderMarkdown(props.content || ""));
</script>

<template>
  <div class="markdown-message" v-html="rendered"></div>
</template>
