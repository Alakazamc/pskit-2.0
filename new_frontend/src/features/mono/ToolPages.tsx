import * as Tabs from "@radix-ui/react-tabs";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Atom, Clock3, Database, ExternalLink, Search } from "lucide-react";
import { useLayoutEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import type { McpResult, Project, ResearchApi } from "../../api/types";
import { useLanguage } from "../../i18n/LanguageProvider";
import { errorTranslationKey } from "../../i18n/errors";
import { ToolAgentAction } from "./ToolAgentAction";
import { CatalogCard } from "../../components/catalog/CatalogCard";
import { CatalogSearch } from "../../components/catalog/CatalogSearch";
import { DetailPanel } from "../../components/catalog/DetailPanel";
import { GenericToolPage } from "./GenericToolPage";
import { StructureViewerPage } from "./StructureViewerPage";

export function ToolDirectory({ api, userId, projects, selectedName, viewer = false, theme }: {
  api: ResearchApi; userId: string; projects: Project[]; selectedName?: string;
  viewer?: boolean; theme: "light" | "dark";
}) {
  const { t } = useLanguage();
  const tools = useQuery({ queryKey: ["mcp-tools", userId], queryFn: api.getMcpTools });
  const [search, setSearch] = useState("");
  const navigate = useNavigate();
  const matches = (name: string, description = "") => `${name} ${description}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase());
  const filtered = (tools.data ?? []).filter((tool) => matches(tool.name, tool.description));
  const showViewer = matches(t("tools.viewerTitle"), t("tools.viewerCardDescription"));
  const selected = tools.data?.find((tool) => tool.name === selectedName);
  return <div className="mono-page-scroll"><div className="mono-page-content">
    <CatalogSearch value={search} onChange={setSearch} label={t("tools.searchDirectory")} />
    {tools.isLoading && <p role="status">{t("workspace.loadingCatalog")}</p>}
    {tools.isError && <p className="mono-form-error" role="alert">{t("tools.loadFailed")}</p>}
    <div className="catalog-entry-grid">
      {showViewer && <CatalogCard title={t("tools.viewerTitle")} description={t("tools.viewerCardDescription")} icon={<Atom />} to="/tools/structure" />}
      {filtered.map((tool) => <CatalogCard key={tool.name} title={tool.name} description={tool.description} icon={<Database />} to={tool.name === "search_pdb" ? "/tools/pdb" : `/tools/run/${encodeURIComponent(tool.name)}`} />)}
    </div>
    {!tools.isLoading && !tools.isError && !showViewer && filtered.length === 0 && <p className="mono-muted">{t("catalog.noMatches")}</p>}
    {(selectedName || viewer) && <ToolDetails key={viewer ? "viewer" : selectedName} title={viewer ? t("tools.viewerTitle") : selectedName!}
      description={viewer ? t("tools.viewerCardDescription") : selected?.description} onClose={() => navigate("/tools")}
      api={api} userId={userId} projects={projects} tool={selectedName}>
      {viewer ? <StructureViewerPage theme={theme} /> : selectedName === "search_pdb" ? <PdbWorkspace api={api} userId={userId} /> : <GenericToolPage api={api} userId={userId} name={selectedName!} />}
    </ToolDetails>}
  </div></div>;
}

function ToolDetails({ title, description, onClose, api, userId, projects, tool, children }: {
  title: string; description?: string; onClose: () => void; api: ResearchApi; userId: string;
  projects: Project[]; tool?: string; children: React.ReactNode;
}) {
  const { t } = useLanguage();
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = tool && searchParams.get("tab") === "history" ? "history" : "run";
  const body = useRef<HTMLDivElement>(null);
  const focusPending = useRef(false);
  const focusArgument = () => body.current?.querySelector<HTMLElement>("input:not([disabled]),textarea:not([disabled]),select:not([disabled])")?.focus();
  useLayoutEffect(() => {
    if (tab === "run" && focusPending.current) {
      focusPending.current = false;
      focusArgument();
    }
  }, [tab]);
  const selectTab = (value: string) => setSearchParams((previous) => {
    const next = new URLSearchParams(previous);
    if (value === "history") next.set("tab", "history"); else next.delete("tab");
    return next;
  }, { replace: true });
  const edit = () => {
    if (tab === "run") focusArgument();
    else { focusPending.current = true; selectTab("run"); }
  };
  return <DetailPanel open onOpenChange={(open) => { if (!open) onClose(); }} title={title} description={description} onEdit={edit}>
    <div ref={body}><Tabs.Root value={tab} onValueChange={selectTab}>
      {tool && <Tabs.List className="catalog-detail-tabs" aria-label={title}>
        <Tabs.Trigger value="run">{t("tools.arguments")}</Tabs.Trigger><Tabs.Trigger value="history">{t("tools.history")}</Tabs.Trigger>
      </Tabs.List>}
      <Tabs.Content value="run" forceMount hidden={tab !== "run"}>{children}</Tabs.Content>
      {tool && <Tabs.Content value="history"><ToolRunHistory api={api} projects={projects} userId={userId} tool={tool} /></Tabs.Content>}
    </Tabs.Root></div>
  </DetailPanel>;
}

export function ToolRunHistory({ api, projects, userId, tool }: { api: ResearchApi; projects: Project[]; userId: string; tool: string }) {
  const { t } = useLanguage();
  const queryClient = useQueryClient();
  const runs = useQuery({ queryKey: ["tool-runs", userId, tool], queryFn: () => api.getToolRuns(tool) });
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
  const tools = useQuery({ queryKey: ["mcp-tools", userId], queryFn: api.getMcpTools });
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
  return <div className="mono-page-scroll"><div className="mono-page-content"><div className="mono-tool-layout"><section className="mono-panel"><div className="mono-panel-heading"><div><Search size={18} /><h2>{t("tools.searchConditions")}</h2></div></div><form className="mono-form" onSubmit={search}><label>{t("tools.proteinQuery")}<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("tools.queryExample")} required /></label><label>{t("tools.experimentMethod")}<select defaultValue="all" disabled><option value="all">{t("tools.allMethods")}</option><option value="xray">X-ray</option><option value="cryo">Cryo-EM</option><option value="nmr">NMR</option></select></label><p className="mono-field-note">{t("tools.methodUnavailable")}</p><button className="mono-button primary" type="submit" disabled={!query.trim() || busy || !available}>{t(busy ? "tools.searching" : "tools.search")}<ArrowRight size={16} /></button>{tools.isSuccess && !available && <p className="mono-form-error">{t("tools.pdbUnavailable")}</p>}{tools.isError && <p className="mono-form-error">{t("tools.loadFailed")}</p>}{error && <p className="mono-form-error" role="alert">{error}</p>}</form></section><section className="mono-panel mono-result-panel"><div className="mono-panel-heading"><div><Database size={18} /><h2>{t("tools.searchResults")}</h2></div>{result && <span className="mono-muted">{t("tools.hitCount", { count: hits.length })}</span>}</div>{result ? <><p className="mono-field-note">{t("tools.sourceNote")}</p>{hits.length ? <div className="mono-result-list">{hits.map((hit, index) => <div key={String(hit.pdb_id ?? index)}><strong>{String(hit.pdb_id ?? t("tools.missingId"))}</strong><span>{String(hit.title ?? t("tools.missingTitle"))}</span>{typeof hit.score === "number" && <small>{t("tools.matchScore", { score: hit.score })}</small>}{typeof hit.pdb_id === "string" && <div className="mono-result-links"><Link to={`/tools/structure?pdb=${encodeURIComponent(hit.pdb_id)}`}>{t("tools.view3d")} <ArrowRight size={13} /></Link><a href={`https://www.rcsb.org/structure/${encodeURIComponent(hit.pdb_id)}`} target="_blank" rel="noreferrer">{t("tools.openPdb")} <ExternalLink size={13} /></a></div>}</div>)}</div> : <p>{t("tools.noHits")}</p>}{result.run_id && <Link className="mono-button" to="/tools/pdb?tab=history">{t("tools.saveFromRuns")} <ArrowRight size={15} /></Link>}<ToolAgentAction api={api} tool="search_pdb" result={result.result} /><details className="mono-raw-result"><summary>{t("tools.viewRaw")}</summary><pre>{JSON.stringify(result.result, null, 2)}</pre></details></> : <div className="mono-result-empty"><Database size={26} /><h3>{t("tools.waitingSearch")}</h3><p>{t("tools.waitingSearchDescription")}</p></div>}</section></div></div></div>;
}
