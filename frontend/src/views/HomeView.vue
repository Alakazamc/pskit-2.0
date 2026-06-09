<script setup lang="ts">
import { RouterLink } from "vue-router";
</script>

<template>
  <section class="hero-grid">
    <div class="hero-copy">
      <h2>AI-native protein-nucleic acid analysis workbench</h2>
      <p>
        PSKit 2.0 rebuilds the original platform around authenticated agent workflows,
        retrieval-augmented system knowledge, durable task execution, and downloadable
        scientific artifacts.
      </p>
      <div class="button-row">
        <RouterLink to="/agent" class="primary-button">Open Agent</RouterLink>
        <RouterLink to="/about" class="ghost-button">Read system design</RouterLink>
      </div>
    </div>
    <div class="hero-console">
      <div class="console-header">
        <span>tool timeline</span>
        <strong>7U5E RNA binding workflow</strong>
      </div>
      <div class="timeline">
        <div class="timeline-row ok">download_pdb_file · 7u5e.cif</div>
        <div class="timeline-row ok">split_complex · protein/RNA chains</div>
        <div class="timeline-row running">predict_binding_sites · queued task</div>
        <div class="timeline-row">read_result_file · CSV report</div>
      </div>
    </div>
  </section>

  <section class="feature-grid">
    <article>
      <h3>Agent + Tools</h3>
      <p>OpenAI-compatible LLM calls drive typed tools for PDB, UniProt, RNAcentral, structures, reports, and long model jobs.</p>
    </article>
    <article>
      <h3>RAG Knowledge</h3>
      <p>Project knowledge is retrieved before each answer so the agent can explain its own models, constraints, and failure modes.</p>
    </article>
    <article>
      <h3>Runtime Tasks</h3>
      <p>Binding-site prediction, PAIR, empirical features, and AlphaFold3 run as persisted tasks with artifacts and logs.</p>
    </article>
  </section>
</template>
