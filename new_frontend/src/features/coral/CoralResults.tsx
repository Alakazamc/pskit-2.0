import { ArrowDownToLine, Dna } from "lucide-react";
import { useMemo, useState } from "react";
import type { ComputeJob, ResearchApi } from "../../api/types";
import { useLanguage } from "../../i18n/LanguageProvider";
import { ToolAgentAction } from "../mono/ToolAgentAction";
import { coralDatasetFiles, coralResult } from "./coralResult";

export function CoralResults({ api, job }: { api: ResearchApi; job?: ComputeJob }) {
  const { t, language } = useLanguage();
  const result = useMemo(() => job ? coralResult(job) : null, [job]);
  const [error, setError] = useState("");
  const [rawOpen, setRawOpen] = useState(false);
  const save = (blob: Blob, name: string) => {
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a"); link.href = url; link.download = name; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const download = async (artifact?: { id: string; name: string }) => {
    setError("");
    try {
      if (artifact) save(await api.downloadArtifact(artifact.id), artifact.name);
      else if (job) {
        const files = coralDatasetFiles(job);
        if (files.length) save(new Blob(files, { type: "text/plain" }), files[0].name.replace(/-1\.fasta$/, ".fasta"));
      }
    } catch { setError(t("coral.datasetFailed")); }
  };
  if (!job || !result) return <section className="coral-results coral-results-empty">
    <Dna size={25} strokeWidth={1.3} aria-hidden="true" /><h3>{t("coral.result")}</h3><p>{t("coral.empty")}</p>
  </section>;
  const length = result.min === null ? "—" : result.min === result.max ? String(result.min) : `${result.min}–${result.max}`;
  const presets = language === "zh" ? [
    { label: t("coral.analyze"), instruction: "分析本次 CORAL RNA 候选序列：读取完整附件，统计长度、碱基组成、GC 含量、重复与序列多样性；说明数据质量和分析局限，并建议下一步。" },
    { label: t("coral.select"), instruction: "为本次 CORAL RNA 候选序列制定并执行候选筛选：读取完整附件，检查重复、长度与 GC 分布，提出透明的筛选规则；没有结合证据时不要声称预测了亲和力。" },
    { label: t("coral.validate"), instruction: "根据本次 CORAL 输入与生成结果设计验证方案：说明所需结构、阴性对照、评价指标及可行的实验或计算验证步骤；把已完成的生成与尚未验证的结论区分清楚。" },
  ] : [
    { label: t("coral.analyze"), instruction: "Analyze these CORAL RNA candidates using the full attached dataset: length, base composition, GC content, duplicates and sequence diversity. State data quality, limitations and next steps." },
    { label: t("coral.select"), instruction: "Read the full CORAL dataset and propose transparent candidate selection rules using duplicates, length and GC distributions. Apply the rules where possible. Do not infer binding affinity without binding evidence." },
    { label: t("coral.validate"), instruction: "Design validation for this CORAL target and generated candidates: structures needed, negative controls, evaluation metrics and feasible experiments or computational checks. Distinguish generation from validated findings." },
  ];
  const summary = {
    job_id: job.id, capability: job.capability.id, version: job.capability.version,
    sequence_count: result.count, min_length: result.min, max_length: result.max,
    full_dataset_available: result.complete, preview: result.sequences.slice(0, 5),
    artifacts: result.artifacts, usage: result.usage,
  };
  return <section className="coral-results">
    <div className="coral-result-heading"><div><h3>{t("coral.result")}</h3><div className="coral-result-summary"><strong>{t("coral.sequenceCount", { count: result.count.toLocaleString(language) })}</strong><span>{t("coral.lengthValue", { length })}</span></div></div>
      <div className="coral-result-actions">{result.complete && <button type="button" aria-label={t("coral.download")} title={t("coral.download")} className="coral-text-action coral-download-main" onClick={() => void download()}><ArrowDownToLine size={14} />FASTA</button>}
        <div className="coral-agent-action"><ToolAgentAction key={job.id} api={api} tool="CORAL" input={job.arguments} result={summary} presets={presets} prepareAttachments={() => coralDatasetFiles(job)} /></div>
      </div>
    </div>
    <p className="coral-result-target">{t("coral.target", { pdb: String(job.arguments.pdb_id ?? "—"), chain: String(job.arguments.chain ?? "—") })}</p>
    {!result.complete && <p className="coral-note">{t("coral.partial")}</p>}
    {result.sequences.length ? <><p className="coral-preview-label">{t("coral.preview", { count: Math.min(5, result.sequences.length) })}</p>
      <ol className="coral-sequences" tabIndex={0} aria-label={t("coral.preview", { count: Math.min(5, result.sequences.length) })}>{result.sequences.slice(0, 5).map((item, index) => <li key={index}><span>{item.id}</span><code>{item.sequence}</code></li>)}</ol>
    </> : <p className="coral-note">{t("coral.noSequences")}</p>}
    <div className="coral-downloads">
      {result.artifacts.map((artifact) => <button type="button" key={artifact.id} className="coral-text-action" onClick={() => void download(artifact)}><ArrowDownToLine size={14} />{artifact.name}</button>)}
    </div>
    {error && <p role="alert" className="mono-form-error">{error}</p>}
    <details className="coral-raw" onToggle={(event) => setRawOpen(event.currentTarget.open)}><summary>{t("tools.viewRaw")}</summary>{rawOpen && <pre>{JSON.stringify(job.report, null, 2)}</pre>}</details>
  </section>;
}
