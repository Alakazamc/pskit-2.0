import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, PenLine, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { ResearchApi } from "../../api/types";
import { useLanguage } from "../../i18n/LanguageProvider";
import { CoralProgress } from "./CoralProgress";
import { CoralResults } from "./CoralResults";
import { coralActive, coralBounds, coralCapabilityId } from "./coralResult";

export function CoralWorkspace({ api, userId }: { api: ResearchApi; userId: string }) {
  const { t } = useLanguage();
  const cache = useQueryClient();
  const [params, setParams] = useSearchParams();
  const jobId = params.get("job");
  const catalog = useQuery({ queryKey: ["compute-capabilities", userId], queryFn: api.getComputeCapabilities });
  const capability = catalog.data?.find((item) => item.id === coralCapabilityId);
  const job = useQuery({ queryKey: ["compute-job", userId, jobId], queryFn: () => api.getComputeJob(jobId!), enabled: !!jobId,
    refetchInterval: (query) => !query.state.error && query.state.data?.capability.id === coralCapabilityId && coralActive(query.state.data?.status) ? 1000 : false });
  const currentJob = job.data?.capability.id === coralCapabilityId ? job.data : undefined;
  const [pdb, setPdb] = useState("");
  const [chain, setChain] = useState("A");
  const [count, setCount] = useState<string | null>(null);
  const [length, setLength] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const proteinField = useRef<HTMLInputElement>(null);
  const restored = useRef<string | null>(null);
  useEffect(() => {
    const current = currentJob;
    if (!current || restored.current === current.id) return;
    restored.current = current.id;
    setEditing(false);
    if (typeof current.arguments.pdb_id === "string") setPdb(current.arguments.pdb_id);
    if (typeof current.arguments.chain === "string") setChain(current.arguments.chain);
    if (typeof current.arguments.num_samples === "number") setCount(String(current.arguments.num_samples));
    if (typeof current.arguments.length === "number") setLength(String(current.arguments.length));
    setError("");
  }, [currentJob]);
  const pending = useRef<{ signature: string; key: string } | null>(null);
  const sending = useRef(false);
  const countBounds = coralBounds(capability, "num_samples"), lengthBounds = coralBounds(capability, "length");
  const requestedCount = count ?? String(Math.max(countBounds.min, Math.min(10000, countBounds.max ?? 10000)));
  const requestedLength = length ?? String(Math.max(lengthBounds.min, Math.min(100, lengthBounds.max ?? 100)));
  const active = coralActive(currentJob?.status);
  const completed = currentJob?.status === "completed" && currentJob.report?.status === "completed";
  const original = currentJob?.arguments;
  const unchanged = completed && pdb.trim().toUpperCase() === original?.pdb_id && chain.trim() === original?.chain &&
    Number(requestedCount) === original?.num_samples && Number(requestedLength) === original?.length;
  const edit = () => {
    setEditing(true);
    requestAnimationFrame(() => proteinField.current?.focus());
  };
  const run = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!capability || sending.current || active) return;
    const num_samples = Number(requestedCount), size = Number(requestedLength);
    if (!/^[a-zA-Z0-9]{4}$/.test(pdb.trim()) || !chain.trim() || chain.trim().length > 4 ||
      !Number.isInteger(num_samples) || num_samples < countBounds.min || num_samples > (countBounds.max ?? Infinity) ||
      !Number.isInteger(size) || size < lengthBounds.min || size > (lengthBounds.max ?? Infinity)) {
      setError(t("coral.invalidInput")); return;
    }
    sending.current = true; setBusy(true); setError("");
    const payload = { capability_id: capability.id, version: capability.version,
      arguments: { pdb_id: pdb.trim().toUpperCase(), chain: chain.trim(), num_samples, length: size },
      budget: capability.max_budget ?? { cpu_core_ms: 0, gpu_device_ms: 0 } };
    const signature = JSON.stringify(payload);
    pending.current = pending.current?.signature === signature ? pending.current : { signature, key: crypto.randomUUID() };
    try {
      const created = await api.submitComputeJob(payload, pending.current.key);
      cache.setQueryData(["compute-job", userId, created.id], created);
      setParams((previous) => { const next = new URLSearchParams(previous); next.set("job", created.id); return next; }, { replace: true });
      pending.current = null;
      void cache.invalidateQueries({ queryKey: ["compute-history", userId, coralCapabilityId] });
      void cache.invalidateQueries({ queryKey: ["usage", userId] });
    } catch { setError(t("coral.runFailed")); }
    finally { sending.current = false; setBusy(false); }
  };
  const cancel = async () => {
    if (!jobId || busy) return;
    setBusy(true); setError("");
    try { cache.setQueryData(["compute-job", userId, jobId], await api.cancelComputeJob(jobId)); }
    catch { setError(t("coral.runFailed")); }
    finally { setBusy(false); }
  };
  return <div className="coral-workspace">
    <CoralProgress job={currentJob} submitting={busy && !active} />
    <div className="coral-layout"><div className="coral-parameters" data-collapsed={completed && !editing}>
      {completed && !editing && <div className="coral-mobile-summary"><div><strong>{pdb} · {chain}</strong><span>{requestedLength} nt · {Number(requestedCount).toLocaleString()}</span></div><button type="button" className="coral-text-action" data-coral-expand onClick={edit}><PenLine size={14} />{t("coral.editInput")}</button></div>}
      <form className="coral-input" onSubmit={(event) => void run(event)}>
      <h3>{t("coral.input")}</h3><p className="coral-note">{t("coral.pdbHint")}</p>
      <div className="coral-fields-pair coral-protein-fields"><label>{t("coral.pdb")}<input ref={proteinField} aria-label={t("coral.pdb")} value={pdb} onChange={(event) => setPdb(event.target.value)} placeholder="1A9N" maxLength={4} required disabled={active || busy} autoComplete="off" /></label>
        <label>{t("coral.chain")}<input aria-label={t("coral.chain")} value={chain} onChange={(event) => setChain(event.target.value)} maxLength={4} required disabled={active || busy} autoComplete="off" /></label></div>
      <div className="coral-fields-pair"><label>{t("coral.length")}<input aria-label={t("coral.length")} type="number" step="1" min={lengthBounds.min} max={lengthBounds.max} value={requestedLength} onChange={(event) => setLength(event.target.value)} disabled={active || busy} required /></label>
        <label>{t("coral.count")}<input aria-label={t("coral.count")} type="number" step="1" min={countBounds.min} max={countBounds.max} value={requestedCount} onChange={(event) => setCount(event.target.value)} disabled={active || busy} required /></label></div>
      {capability && (capability.max_budget?.gpu_device_ms ?? 0) > 0 && <p className="coral-budget">{t("coral.gpuLimit", { minutes: Math.ceil(capability.max_budget!.gpu_device_ms! / 60000) })}</p>}
      {active ? <button className="mono-button coral-run-button" type="button" disabled={busy || job.data?.status === "cancelling"} onClick={() => void cancel()}><Square size={13} />{t(job.data?.status === "cancelling" ? "coral.cancelling" : "coral.cancel")}</button>
        : <button className={`mono-button ${unchanged ? "" : "primary"} coral-run-button`} type="submit" disabled={busy || !capability || !pdb.trim()}>{t(unchanged ? "coral.regenerate" : "coral.generate")}<ArrowRight size={15} /></button>}
      {catalog.isSuccess && !capability && <p className="coral-note">{t("coral.unavailable")}</p>}
      {catalog.isError && <p role="alert" className="mono-form-error">{t("coral.loadFailed")}</p>}
      {error && <p role="alert" className="mono-form-error">{error}</p>}
    </form></div><CoralResults key={jobId} api={api} job={currentJob} /></div>
    {job.data && !currentJob && <p role="alert" className="mono-form-error">{t("coral.jobMismatch")}</p>}
    {job.isError && <div className="coral-reconnect" role="alert"><span>{t("coral.readFailed")}</span><button type="button" className="coral-text-action" onClick={() => void job.refetch()}>{t("coral.retry")}</button></div>}
  </div>;
}
