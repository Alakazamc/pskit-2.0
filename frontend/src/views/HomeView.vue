<script setup lang="ts">
import { RouterLink } from "vue-router";
</script>

<template>
  <section class="hero-grid">
    <div class="hero-copy">
      <h2>面向蛋白质-核酸分析的 AI 原生工作台</h2>
      <p>
        PSKit 2.0 在旧版科学计算能力之上，重构了账号隔离、智能体工具调用、
        RAG 项目知识库、持久化任务队列和可下载分析结果。
      </p>
      <div class="button-row">
        <RouterLink
          to="/agent"
          class="primary-button"
        >
          打开智能体
        </RouterLink>
        <RouterLink
          to="/about"
          class="ghost-button"
        >
          查看技术设计
        </RouterLink>
      </div>
    </div>
    <div class="hero-console">
      <div class="console-header">
        <span>工具时间线</span>
        <strong>7U5E RNA 结合分析流程</strong>
      </div>
      <div class="timeline">
        <div class="timeline-row ok">
          download_pdb_file · 7u5e.cif
        </div>
        <div class="timeline-row ok">
          split_complex · protein/RNA chains
        </div>
        <div class="timeline-row running">
          predict_binding_sites · queued task
        </div>
        <div class="timeline-row">
          read_result_file · CSV report
        </div>
      </div>
    </div>
  </section>

  <section class="feature-grid">
    <article>
      <h3>智能体 + 工具</h3>
      <p>OpenAI-compatible LLM 驱动 PDB、UniProt、RNAcentral、结构处理、报告生成和长任务模型工具。</p>
    </article>
    <article>
      <h3>RAG 项目知识库</h3>
      <p>每次回答前检索 PSKit 自身文档，让智能体理解模型、运行约束、工具边界和常见错误。</p>
    </article>
    <article>
      <h3>可追踪任务</h3>
      <p>结合位点预测、PAIR、经验特征和 AlphaFold3 以持久化任务运行，并保留结果文件与日志。</p>
    </article>
  </section>
</template>
