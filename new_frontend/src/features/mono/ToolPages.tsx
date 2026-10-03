import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Atom, Clock3, Database, ExternalLink, Search } from "lucide-react";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { McpResult, Project, ResearchApi } from "../../api/types";
import { useLanguage } from "../../i18n/LanguageProvider";
import { errorTranslationKey } from "../../i18n/errors";
import { ToolAgentAction } from "./ToolAgentAction";

export function ToolDirectory({ api }: { api: ResearchApi }) {
  const { t } = useLanguage();
  const tools = useQuery({ queryKey: ["mcp-tools"], queryFn: api.getMcpTools });
  return <div className="mono-page-scroll"><div className="mono-page-content">
    {tools.isError && <p className="mono-form-error" role="alert">{t("tools.loadFailed")}</p>}
    <div className="mono-card-grid tools"><Link className="mono-card tool-card" to="/tools/structure"><div className="mono-tool-icon"><Atom size={22} /></div><h2>{t("tools.viewerTitle")}</h2><p>{t("tools.viewerCardDescription")}</p><span>{t("tools.open")} <ArrowRight size={15} /></span></Link>{(tools.data ?? []).map((tool) => <Link className="mono-card tool-card" key={tool.name} to={tool.name === "search_pdb" ? "/tools/pdb" : `/tools/run/${encodeURIComponent(tool.name)}`}><div className="mono-tool-icon"><Database size={22} /></div><h2>{tool.name}</h2><p>{tool.description}</p><span>{t("tools.open")} <ArrowRight size={15} /></span></Link>)}</div>
  </div></div>;
}

export function MyRuns({ api, projects, userId }: { api: ResearchApi; projects: Project[]; userId: string }) {
  const { t } = useLanguage();
  const queryClient = useQueryClient();
  const runs = useQuery({ queryKey: ["tool-runs", userId], queryFn: api.getToolRuns });
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState("");
  const save = async (runId: string, projectId: string) => {
    if (!projectId) return;
    setSaving(runId); setError("");
    try {
      await api.saveToolRunToProject(runId, projectId);
      await queryClient.invalidateQueries({ queryKey: ["tool-runs", userId] });
    } catch { setError(t("tools.saveFailed")); }
    finally { setSaving(null); }
  };
  return <div className="mono-page-scroll"><div className="mono-page-content">{error && <p role="alert" className="mono-form-error">{error}</p>}{runs.isLoading && <div className="mono-loading-page" role="status">{t("tools.loadingRuns")}</div>}{runs.isError && <div className="mono-empty-panel"><h2>{t("tools.readFailed")}</h2><p>{t("tools.checkConnection")}</p></div>}{runs.data?.length ? <div className="mono-panel mono-run-list">{runs.data.map((run) => <div className="mono-run-item" key={run.id}><div><Database size={17} /><strong>{run.title}</strong><small>{new Date(run.created_at).toLocaleString()}</small></div><div className="mono-run-actions">{run.project_id ? <span className="mono-chip">{t("tools.saved", { name: projects.find((project) => project.id === run.project_id)?.name ?? t("project.title") })}</span> : <select aria-label={t("tools.saveRunLabel", { name: run.title })} value="" disabled={saving === run.id || projects.length === 0} onChange={(event) => void save(run.id, event.target.value)}><option value="">{t("tools.saveToProject")}</option>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select>}</div><details><summary>{t("tools.viewRawResult")}</summary><pre>{JSON.stringify(run.result, null, 2)}</pre></details></div>)}</div> : !runs.isLoading && !runs.isError && <div className="mono-empty-panel"><Clock3 size={24} /><h2>{t("tools.noRuns")}</h2><p>{t("tools.noRunsDescription")}</p></div>}</div></div>;
}

export function PdbWorkspace({ api, userId }: { api: ResearchApi; userId: string }) {
  const { t } = useLanguage();
  const queryClient = useQueryClient();
  const tools = useQuery({ queryKey: ["mcp-tools"], queryFn: api.getMcpTools });
  const available = tools.data?.some((tool) => tool.name === "search_pdb");
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<McpResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pendingInvocation = useRef<{ query: string; key: string } | null>(null);
  const search = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!query.trim() || busy) return;
    setBusy(true); setError(""); setResult(null);
    const cleanQuery = query.trim();
    const key = pendingInvocation.current?.query === cleanQuery
      ? pendingInvocation.current.key : crypto.randomUUID();
    pendingInvocation.current = { query: cleanQuery, key };
    try {
      const response = await api.invokeMcpTool("search_pdb", { query: cleanQuery }, key);
      pendingInvocation.current = null;
      setResult(response);
      await queryClient.invalidateQueries({ queryKey: ["tool-runs", userId] });
    } catch (caught) { setError(t(errorTranslationKey(caught) ?? "tools.pdbFailed")); }
    finally { setBusy(false); }
  };
  const hits = Array.isArray(result?.result.hits) ? result.result.hits as Record<string, unknown>[] : [];
  return <div className="mono-page-scroll"><div className="mono-page-content"><div className="mono-tool-layout"><section className="mono-panel"><div className="mono-panel-heading"><div><Search size={18} /><h2>{t("tools.searchConditions")}</h2></div></div><form className="mono-form" onSubmit={search}><label>{t("tools.proteinQuery")}<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("tools.queryExample")} required /></label><label>{t("tools.experimentMethod")}<select defaultValue="all" disabled><option value="all">{t("tools.allMethods")}</option><option value="xray">X-ray</option><option value="cryo">Cryo-EM</option><option value="nmr">NMR</option></select></label><p className="mono-field-note">{t("tools.methodUnavailable")}</p><button className="mono-button primary" type="submit" disabled={!query.trim() || busy || !available}>{t(busy ? "tools.searching" : "tools.search")}<ArrowRight size={16} /></button>{tools.isSuccess && !available && <p className="mono-form-error">{t("tools.pdbUnavailable")}</p>}{tools.isError && <p className="mono-form-error">{t("tools.loadFailed")}</p>}{error && <p className="mono-form-error" role="alert">{error}</p>}</form></section><section className="mono-panel mono-result-panel"><div className="mono-panel-heading"><div><Database size={18} /><h2>{t("tools.searchResults")}</h2></div>{result && <span className="mono-muted">{t("tools.hitCount", { count: hits.length })}</span>}</div>{result ? <><p className="mono-field-note">{t("tools.sourceNote")}</p>{hits.length ? <div className="mono-result-list">{hits.map((hit, index) => <div key={String(hit.pdb_id ?? index)}><strong>{String(hit.pdb_id ?? t("tools.missingId"))}</strong><span>{String(hit.title ?? t("tools.missingTitle"))}</span>{typeof hit.score === "number" && <small>{t("tools.matchScore", { score: hit.score })}</small>}{typeof hit.pdb_id === "string" && <div className="mono-result-links"><Link to={`/tools/structure?pdb=${encodeURIComponent(hit.pdb_id)}`}>{t("tools.view3d")} <ArrowRight size={13} /></Link><a href={`https://www.rcsb.org/structure/${encodeURIComponent(hit.pdb_id)}`} target="_blank" rel="noreferrer">{t("tools.openPdb")} <ExternalLink size={13} /></a></div>}</div>)}</div> : <p>{t("tools.noHits")}</p>}{result.run_id && <Link className="mono-button" to="/tools/runs">{t("tools.saveFromRuns")} <ArrowRight size={15} /></Link>}<ToolAgentAction api={api} tool="search_pdb" result={result.result} /><details className="mono-raw-result"><summary>{t("tools.viewRaw")}</summary><pre>{JSON.stringify(result.result, null, 2)}</pre></details></> : <div className="mono-result-empty"><Database size={26} /><h3>{t("tools.waitingSearch")}</h3><p>{t("tools.waitingSearchDescription")}</p></div>}</section></div></div></div>;
}
