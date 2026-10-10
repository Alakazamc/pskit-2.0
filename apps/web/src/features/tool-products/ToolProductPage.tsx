import * as Tabs from "@radix-ui/react-tabs";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Clock3, Square, Sparkles } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import type { ArtifactRef, ContextRef, ResearchApi, ToolProductHandoff, ToolProductRun } from "../../api/types";
import { useLanguage } from "../../i18n/LanguageProvider";
import { errorTranslationKey } from "../../i18n/errors";
import { conversationDraftScope, emptyComposerDraft, writeComposerDraft } from "../chat/composerDrafts";
import { personalSessionPath } from "../mono/sessionPaths";
import { ToolPageHeader } from "../mono/ToolPageHeader";
import { PageLoading, PageLoadError } from "../../components/PageLoading";
import { ToolUiRenderer } from "./ToolUiRenderer";
import { localized, projectActionArguments, resolvePointer, setFormPointer, type ToolForm, type ToolUiField, type ToolUiSchema } from "./toolUiSchema";

const activeStatuses = new Set(["queued", "running", "cancelling"]);
const terminalStatuses = new Set(["completed", "failed", "cancelled"]);

function fileFields(schema: ToolUiSchema): ToolUiField[] {
  const found: ToolUiField[] = [];
  const visit = (fields: ToolUiField[]) => fields.forEach((field) => {
    if (field.component === "file-upload") found.push(field);
    visit(field.fields ?? []);
  });
  (schema.sections ?? []).forEach((section) => visit(section.fields));
  return found;
}

function formSignature(form: ToolForm): string {
  return JSON.stringify(form, (_key, value) => value instanceof File
    ? { name: value.name, size: value.size, type: value.type, lastModified: value.lastModified }
    : value);
}

export function ToolProductPage({ api, slug, userId, theme, onBack }: {
  api: ResearchApi; slug: string; userId: string; theme: "dark" | "light"; onBack: () => void;
}) {
  const { language, t } = useLanguage();
  const navigate = useNavigate();
  const cache = useQueryClient();
  const [params, setParams] = useSearchParams();
  const product = useQuery({
    queryKey: ["tool-product", userId, slug], queryFn: () => api.getToolProduct(slug),
    initialData: () => cache.getQueryData<Awaited<ReturnType<ResearchApi["getToolProducts"]>>>(["tool-products", userId])?.items?.find((item) => item.slug === slug),
    initialDataUpdatedAt: () => cache.getQueryState(["tool-products", userId])?.dataUpdatedAt,
    refetchOnMount: "always",
  });
  const [form, setForm] = useState<ToolForm>({});
  const [run, setRun] = useState<ToolProductRun | null>(null);
  const [events, setEvents] = useState<Awaited<ReturnType<ResearchApi["getToolProductRunEvents"]>>>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const runId = params.get("run");
  const tab = params.get("tab") === "history" ? "history" : "run";
  const history = useQuery({ queryKey: ["tool-product-runs", userId, slug], queryFn: () => api.getToolProductRuns(slug), enabled: tab === "history" });
  const pendingStart = useRef<{ signature: string; key: string } | null>(null);
  const pendingHandoff = useRef<{
    id: string;
    runId: string;
    sessionId?: string;
    context?: ToolProductHandoff;
    attachments?: ContextRef[];
  } | null>(null);

  useEffect(() => {
    if (!runId) { setRun(null); setEvents([]); return; }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let cursor = 0;
    setEvents([]);
    const poll = async () => {
      try {
        const [snapshot, nextEvents] = await Promise.all([
          api.getToolProductRun(runId), api.getToolProductRunEvents(runId, cursor),
        ]);
        if (cancelled) return;
        setRun(snapshot);
        if (nextEvents.length) {
          cursor = Math.max(cursor, ...nextEvents.map((event) => event.sequence));
          setEvents((previous) => {
            const byId = new Map(previous.map((event) => [event.event_id, event]));
            nextEvents.forEach((event) => byId.set(event.event_id, event));
            return [...byId.values()].sort((left, right) => left.sequence - right.sequence);
          });
        }
        if (!terminalStatuses.has(snapshot.status)) timer = setTimeout(() => void poll(), 600);
      } catch {
        if (!cancelled) setError(t("toolProduct.readFailed"));
      }
    };
    void poll();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [api, runId, t]);

  const selectTab = (value: string) => setParams((previous) => {
    const next = new URLSearchParams(previous);
    if (value === "history") next.set("tab", "history"); else next.delete("tab");
    return next;
  }, { replace: true });

  const materializeFiles = async (schema: ToolUiSchema, source: ToolForm): Promise<ToolForm> => {
    let next = source;
    for (const field of fileFields(schema)) {
      if (!field.input_pointer) continue;
      const selected = resolvePointer({ form: next }, field.input_pointer);
      if (!Array.isArray(selected) || !selected.every((item) => item instanceof File)) continue;
      const uploaded = [];
      for (const file of selected) uploaded.push(await api.uploadFile(file));
      next = setFormPointer(next, field.input_pointer, field.max_files === 1 ? uploaded[0]?.id : uploaded.map((item) => item.id));
    }
    return next;
  };

  const start = async (actionId: string, values: ToolForm) => {
    if (!product.data || busy) return;
    setBusy(true); setError("");
    try {
      const selected = projectActionArguments(product.data.actions, actionId, values);
      const submission = await materializeFiles(product.data.ui_schema, selected);
      setForm(submission);
      const signature = `${actionId}\0${formSignature(submission)}`;
      if (pendingStart.current?.signature !== signature) pendingStart.current = { signature, key: crypto.randomUUID() };
      const created = await api.startToolProductRun(slug, actionId, submission, pendingStart.current.key);
      pendingStart.current = null;
      setRun(created); setEvents([]);
      setParams((previous) => { const next = new URLSearchParams(previous); next.set("run", created.run_id); next.delete("tab"); return next; }, { replace: true });
      void cache.invalidateQueries({ queryKey: ["tool-product-runs", userId, slug] });
    } catch (error) { setError(t(errorTranslationKey(error) ?? "toolProduct.startFailed")); }
    finally { setBusy(false); }
  };

  const cancel = async () => {
    if (!run || busy || !activeStatuses.has(run.status)) return;
    setBusy(true); setError("");
    try { setRun(await api.cancelToolProductRun(run.run_id)); }
    catch { setError(t("toolProduct.cancelFailed")); }
    finally { setBusy(false); }
  };

  const performHandoff = async (handoffId: string) => {
    if (!run || run.status !== "completed" || busy) return;
    setBusy(true); setError("");
    if (pendingHandoff.current?.id !== handoffId || pendingHandoff.current.runId !== run.run_id) {
      pendingHandoff.current = { id: handoffId, runId: run.run_id };
    }
    try {
      const pending = pendingHandoff.current;
      const context = pending.context ?? await api.handoffToolProductRun(run.run_id, handoffId);
      pending.context = context;
      if (!pending.attachments) {
        const attachments: ContextRef[] = [];
        for (const artifact of (context.artifacts ?? []).filter((item) => item.available).slice(0, 10)) {
          const content = await api.downloadArtifact(artifact.id);
          const type = artifact.kind.includes("/") ? artifact.kind : "";
          const uploaded = await api.uploadFile(new File([content], artifact.name, { type }));
          attachments.push({ id: uploaded.id, name: uploaded.name });
        }
        pending.attachments = attachments;
      }
      const title = localized(product.data?.title, language) || slug;
      const createdSession = pending.sessionId
        ? null
        : await api.createSession(null, `${title} · Agent`, true);
      const sessionId = pending.sessionId ?? createdSession!.id;
      pending.sessionId = sessionId;
      if (createdSession) {
        cache.setQueryData(["session", userId, sessionId], createdSession);
        cache.setQueryData<Awaited<ReturnType<ResearchApi["getSessions"]>>>(
          ["sessions", userId, createdSession.project_id],
          (previous = []) => previous.some((item) => item.id === sessionId)
            ? previous
            : [createdSession, ...previous],
        );
      }
      const summary = context.summary.slice(0, 4_000);
      const inputs = JSON.stringify(run.arguments ?? {}).slice(0, 1_000);
      const content = language === "en"
        ? `Continue from Tool Run ${context.run_id}.\n\nRun inputs:\n${inputs}\n\nResult summary:\n${summary}`
        : `继续分析工具运行 ${context.run_id}。\n\n本次运行参数：\n${inputs}\n\n结果摘要：\n${summary}`;
      const persisted = writeComposerDraft(conversationDraftScope(userId, sessionId), {
        ...emptyComposerDraft(),
        content,
        attachments: pending.attachments,
      });
      if (!persisted) throw new Error("COMPOSER_DRAFT_UNAVAILABLE");
      pendingHandoff.current = null;
      navigate(personalSessionPath(sessionId));
    } catch (cause) { setError(t(errorTranslationKey(cause) ?? "toolProduct.handoffFailed")); }
    finally { setBusy(false); }
  };

  const downloadArtifact = async (artifact: ArtifactRef) => {
    if (!artifact.available || busy) return;
    setBusy(true); setError("");
    try {
      const blob = await api.downloadArtifact(artifact.id);
      const url = URL.createObjectURL(blob);
      try {
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = artifact.name;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
      } finally {
        URL.revokeObjectURL(url);
      }
    } catch { setError(t("artifact.downloadFailed")); }
    finally { setBusy(false); }
  };

  const currentSchema = product.data?.ui_schema;
  const title = localized(product.data?.title, language) || (product.isError ? t("mono.pageNotFound") : "");
  const handoffs = useMemo(() => currentSchema?.handoffs ?? [], [currentSchema]);
  const header = <ToolPageHeader title={title} description={localized(product.data?.description, language)} loading={product.isPending} onBack={onBack} />;
  if (product.isPending) return <>{header}<PageLoading label={t("toolProduct.loading")} /></>;
  if (!product.data || !currentSchema) return <>{header}<PageLoadError message={t("toolProduct.notAvailable")} retryLabel={t("loading.retry")} onRetry={() => { void product.refetch(); }} /></>;

  return <article className="mono-tool-detail tool-product-page" aria-label={title}>
    {header}
    <div className="mono-tool-detail-body"><Tabs.Root value={tab} onValueChange={selectTab}>
      <Tabs.List className="catalog-detail-tabs" aria-label={title}><Tabs.Trigger value="run">{t("tools.arguments")}</Tabs.Trigger><Tabs.Trigger value="history">{t("tools.history")}</Tabs.Trigger></Tabs.List>
      <Tabs.Content value="run" forceMount hidden={tab !== "run"}>
        {error && <p role="alert" className="mono-form-error">{error}</p>}
        <ToolUiRenderer schema={currentSchema} form={form} run={run} events={events} onChange={setForm} onAction={(actionId, values) => void start(actionId, values)} onArtifactDownload={(artifact) => void downloadArtifact(artifact)} theme={theme} showHeader={false} resultActions={<>
          {run && activeStatuses.has(run.status) && <button type="button" className="mono-button" disabled={busy || run.status === "cancelling"} onClick={() => void cancel()}><Square size={13} />{t(run.status === "cancelling" ? "toolProduct.stopping" : "toolProduct.stop")}</button>}
          {run?.status === "completed" && handoffs.map((item) => <button type="button" className="mono-button" disabled={busy} key={item.id} onClick={() => void performHandoff(item.id)}><Sparkles size={14} />{localized(item.label, language)}</button>)}
        </>} />
      </Tabs.Content>
      <Tabs.Content value="history"><div className="mono-page-content tool-product-history">
        {history.isPending && <PageLoading label={t("tools.loadingRuns")} />}
        {history.isError && <p role="alert" className="mono-form-error">{t("tools.readFailed")}</p>}
        {history.data?.length ? <div className="mono-run-list">{history.data.map((item) => <button type="button" className="mono-run-item" key={item.run_id} aria-label={`${item.run_id} · ${item.status}`} onClick={() => {
          setParams((previous) => { const next = new URLSearchParams(previous); next.set("run", item.run_id); next.delete("tab"); return next; });
        }}><span><Clock3 size={15} /><strong>{item.run_id}</strong><small>{new Date(item.created_at).toLocaleString()}</small></span><span className="mono-chip">{item.status}</span></button>)}</div>
          : !history.isLoading && !history.isError && <div className="mono-empty-panel"><Clock3 size={22} /><h2>{t("tools.noRuns")}</h2></div>}
      </div></Tabs.Content>
    </Tabs.Root></div>
  </article>;
}
