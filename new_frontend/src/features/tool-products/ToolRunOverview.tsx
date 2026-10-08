import type { ReactNode } from "react";
import { Ban, CheckCircle2, Clock3, LoaderCircle, XCircle } from "lucide-react";
import type { ToolRun, ToolUiField, ToolUiSchema } from "./toolUiSchema";
import { localized } from "./toolUiSchema";
import { useLanguage, type Language } from "../../i18n/LanguageProvider";

const statusIcons = {
  queued: Clock3,
  running: LoaderCircle,
  cancelling: LoaderCircle,
  completed: CheckCircle2,
  failed: XCircle,
  cancelled: Ban,
} as const;

const statusKeys = {
  queued: "toolProduct.status.queued",
  running: "toolProduct.status.running",
  cancelling: "toolProduct.status.cancelling",
  completed: "toolProduct.status.completed",
  failed: "toolProduct.status.failed",
  cancelled: "toolProduct.status.cancelled",
} as const;

function fieldLabels(schema: ToolUiSchema, language: Language): Map<string, string> {
  const labels = new Map<string, string>();
  const visit = (fields: ToolUiField[]) => fields.forEach((field) => {
    const key = field.input_pointer?.match(/^\/form\/([^/]+)$/u)?.[1];
    if (key) labels.set(key, localized(field.label, language));
    visit(field.fields ?? []);
  });
  (schema.sections ?? []).forEach((section) => visit(section.fields));
  return labels;
}

function workflowLabel(schema: ToolUiSchema, run: ToolRun, language: Language): string {
  for (const action of schema.actions) {
    if (!("by_state" in action.target)) continue;
    const selection = action.target.by_state;
    const selected = Object.entries(selection.map).find(([, actionId]) => actionId === run.action_id);
    if (!selected) continue;
    const fields: ToolUiField[] = [];
    const visit = (items: ToolUiField[]) => items.forEach((field) => {
      fields.push(field);
      visit(field.fields ?? []);
    });
    (schema.sections ?? []).forEach((section) => visit(section.fields));
    const field = fields.find((candidate) => candidate.input_pointer === selection.source);
    const option = field?.options?.find((candidate) => String(candidate.value) === selected[0]);
    if (option) return localized(option.label, language);
  }
  return fallbackLabel(run.action_id);
}

function fallbackLabel(key: string): string {
  return key.replaceAll("_", " ").replace(/\b\w/gu, (letter) => letter.toUpperCase());
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "object") {
    try { return JSON.stringify(value); } catch { return "—"; }
  }
  return String(value);
}

function usageSource(run: ToolRun, language: Language): string {
  const labels = language === "en"
    ? { service_reported: "Measured by service", measured: "Measured by platform", estimated: "Estimated", unknown: "Unknown usage source" }
    : { service_reported: "服务端记录用量", measured: "平台测量用量", estimated: "估算用量", unknown: "用量来源未知" };
  return run.usage?.source ? labels[run.usage.source] : "";
}

function elapsed(run: ToolRun): string {
  const duration = Math.max(0, new Date(run.updated_at).getTime() - new Date(run.created_at).getTime());
  if (duration < 1_000) return `${duration} ms`;
  if (duration < 60_000) return `${(duration / 1_000).toFixed(duration < 10_000 ? 1 : 0)} s`;
  return `${Math.floor(duration / 60_000)}m ${Math.round((duration % 60_000) / 1_000)}s`;
}

export function ToolRunOverview({ schema, run, actions }: {
  schema: ToolUiSchema;
  run: ToolRun;
  actions?: ReactNode;
}) {
  const { language, t } = useLanguage();
  const Icon = statusIcons[run.status];
  const labels = fieldLabels(schema, language);
  const inputs = Object.entries(run.arguments ?? {});
  const workflow = workflowLabel(schema, run, language);
  const statusLabel = t(statusKeys[run.status]);
  const source = usageSource(run, language);
  return <div className="tool-run-overview">
    <div className="tool-run-overview-main">
      <span className="tool-run-status-icon" data-status={run.status}><Icon aria-hidden="true" /></span>
      <div>
        <strong>{statusLabel}{["queued", "running", "cancelling"].includes(run.status) ? ` · ${run.progress}%` : ""}</strong>
        <p>{elapsed(run)} · {new Date(run.updated_at).toLocaleString()}</p>
      </div>
      {actions && <div className="tool-run-overview-actions">{actions}</div>}
    </div>
    <div className="tool-run-meta">
      <span title={run.run_id}>{run.run_id}</span>
      {source && <span>{source}</span>}
    </div>
    <section className="tool-run-input-snapshot" aria-labelledby="tool-run-input-title">
      <h2 id="tool-run-input-title">{t("toolProduct.runInputs")}</h2>
      <dl><div><dt>{language === "en" ? "Workflow" : "工作流"}</dt><dd>{workflow}</dd></div>{inputs.map(([key, value]) => <div key={key}>
        <dt>{labels.get(key) || fallbackLabel(key)}</dt>
        <dd>{displayValue(value)}</dd>
      </div>)}</dl>
    </section>
  </div>;
}
