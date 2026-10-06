import { lazy, Suspense } from "react";
import type { ReactNode } from "react";
import { Download, FileText } from "lucide-react";
import type { ArtifactRef } from "../../api/types";
import { useLanguage } from "../../i18n/LanguageProvider";
import {
  applyTransforms,
  conditionMatches,
  localized,
  resolvePointer,
  type ToolEvent,
  type ToolRun,
  type ToolUiResultView,
  type ToolUiSchema,
} from "./toolUiSchema";
import { ToolStageFlow } from "./ToolStageFlow";

const MolstarCanvas = lazy(() => import("../mono/MolstarCanvas"));
const supported = new Set([
  "metric-grid", "stage-flow", "sequence-table", "data-table", "line-chart", "scatter-plot", "heatmap",
  "structure-viewer", "artifact-list", "json-inspector",
]);

function objectRows(value: unknown, limit: number): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.slice(0, limit).filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null && !Array.isArray(item))
    : [];
}

function DataTable({ value, title, limit }: { value: unknown; title: string; limit: number }) {
  const rows = objectRows(value, limit);
  if (!rows.length) return null;
  const columns = [...new Set(rows.flatMap((row) => Object.keys(row)))].slice(0, 20);
  return <div className="tool-ui-table-wrap" tabIndex={0}><table aria-label={title}><thead><tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr></thead>
    <tbody>{rows.map((row, index) => <tr key={index}>{columns.map((column) => <td key={column}>{String(row[column] ?? "")}</td>)}</tr>)}</tbody></table></div>;
}

function MetricGrid({ value }: { value: unknown }) {
  const metrics = typeof value === "object" && value !== null && !Array.isArray(value) ? Object.entries(value) : [];
  if (!metrics.length) return null;
  return <dl className="tool-metric-grid">{metrics.slice(0, 24).map(([name, metric]) => <div key={name}><dt>{name}</dt><dd>{String(metric ?? "")}</dd></div>)}</dl>;
}

function points(value: unknown): Array<{ x: number; y: number }> {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item, index) => {
    if (typeof item === "number") return [{ x: index, y: item }];
    if (typeof item !== "object" || item === null) return [];
    const x = Number((item as Record<string, unknown>).x ?? index);
    const y = Number((item as Record<string, unknown>).y ?? (item as Record<string, unknown>).value);
    return Number.isFinite(x) && Number.isFinite(y) ? [{ x, y }] : [];
  }).slice(0, 500);
}

function Plot({ value, title, scatter }: { value: unknown; title: string; scatter: boolean }) {
  const data = points(value);
  if (!data.length) return null;
  const xs = data.map((point) => point.x), ys = data.map((point) => point.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const position = (point: { x: number; y: number }) => ({
    x: 8 + ((point.x - minX) / (maxX - minX || 1)) * 284,
    y: 112 - ((point.y - minY) / (maxY - minY || 1)) * 104,
  });
  const positioned = data.map(position);
  return <svg className="tool-ui-chart" viewBox="0 0 300 120" role="img" aria-label={title}><title>{title}</title>
    {scatter ? positioned.map((point, index) => <circle key={index} cx={point.x} cy={point.y} r="3" />)
      : <polyline points={positioned.map((point) => `${point.x},${point.y}`).join(" ")} fill="none" />}
  </svg>;
}

function Heatmap({ value, title }: { value: unknown; title: string }) {
  const rows = Array.isArray(value) ? value.filter(Array.isArray).slice(0, 50) as unknown[][] : [];
  if (!rows.length) return null;
  const numeric = rows.flat().map(Number).filter(Number.isFinite);
  const min = Math.min(...numeric), max = Math.max(...numeric);
  const width = Math.max(...rows.map((row) => row.length), 1);
  return <svg className="tool-ui-heatmap" viewBox={`0 0 ${width * 18} ${rows.length * 18}`} role="img" aria-label={title}><title>{title}</title>
    {rows.flatMap((row, y) => row.slice(0, 50).map((item, x) => {
      const opacity = .15 + ((Number(item) - min) / (max - min || 1)) * .85;
      return <rect key={`${x}-${y}`} x={x * 18} y={y * 18} width="16" height="16" opacity={Number.isFinite(opacity) ? opacity : .15} />;
    }))}
  </svg>;
}

function Structure({ value, theme }: { value: unknown; theme: "dark" | "light" }) {
  const id = typeof value === "string" ? value : typeof value === "object" && value !== null
    ? String((value as Record<string, unknown>).pdb_id ?? (value as Record<string, unknown>).id ?? "") : "";
  if (!/^[A-Za-z0-9]{4}$/u.test(id)) return null;
  return <div className="tool-ui-structure"><Suspense fallback={<div role="status">Loading structure…</div>}>
    <MolstarCanvas source={{ type: "pdb", id: id.toUpperCase() }} theme={theme} />
  </Suspense></div>;
}

function JsonPreview({ value, limit }: { value: unknown; limit: number }) {
  const serialized = (() => {
    try { return JSON.stringify(value, null, 2) ?? ""; } catch { return ""; }
  })();
  if (!serialized) return null;
  const bound = Math.min(Math.max(limit, 40), 2_000);
  const truncated = serialized.length > bound;
  return <pre className="tool-ui-json" tabIndex={0}>{serialized.slice(0, bound)}{truncated ? "\n… (truncated)" : ""}</pre>;
}

function ResultContent({ view, value, run, events, title, theme, onArtifactDownload }: { view: ToolUiResultView; value: unknown; run: ToolRun; events: ToolEvent[]; title: string; theme: "dark" | "light"; onArtifactDownload?: (artifact: ArtifactRef) => void }): ReactNode {
  const { language, t } = useLanguage();
  const limit = Math.min(view.preview_limit ?? 100, 10_000);
  if (view.component === "metric-grid") return <MetricGrid value={value} />;
  if (view.component === "stage-flow") return <ToolStageFlow events={events} />;
  if (view.component === "sequence-table" || view.component === "data-table") return <DataTable value={value} title={title} limit={limit} />;
  if (view.component === "line-chart") return <Plot value={value} title={title} scatter={false} />;
  if (view.component === "scatter-plot") return <Plot value={value} title={title} scatter />;
  if (view.component === "heatmap") return <Heatmap value={value} title={title} />;
  if (view.component === "structure-viewer") return <Structure value={value} theme={theme} />;
  if (view.component === "artifact-list") {
    const artifacts = Array.isArray(value) ? value.slice(0, limit) : run.artifacts.slice(0, limit);
    if (!artifacts.length) return null;
    return <ul className="tool-artifact-list">{artifacts.map((artifact, index) => {
      const item = typeof artifact === "object" && artifact !== null ? artifact as Record<string, unknown> : {};
      const id = String(item.id ?? "");
      const name = String(item.name ?? item.id ?? "Artifact");
      const owned = run.artifacts.find((candidate) => candidate.id === id);
      return <li key={id || index}><FileText aria-hidden="true" /><span>{name}</span>
        {owned?.available && onArtifactDownload && <button type="button" className="tool-artifact-download"
          aria-label={t("artifact.downloadName", { name })} title={language === "en" ? `Download ${name}` : `下载 ${name}`}
          onClick={() => onArtifactDownload(owned)}><Download size={15} aria-hidden="true" /></button>}
      </li>;
    })}</ul>;
  }
  if (view.component === "json-inspector") return <JsonPreview value={value} limit={limit} />;
  return <div role="alert" className="tool-ui-error">Unsupported component: {view.component}</div>;
}

function hasResultData(view: ToolUiResultView, value: unknown, run: ToolRun, events: ToolEvent[]): boolean {
  if (view.component === "stage-flow") return events.some((event) => event.type.startsWith("stage."));
  if (view.component === "artifact-list") return (Array.isArray(value) && value.length > 0) || run.artifacts.length > 0;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === "object" && value !== null) return Object.keys(value).length > 0;
  return value !== undefined && value !== null && value !== "";
}

export function ToolResultRenderer({ schema, form, run, events, onArtifactDownload, theme = "light" }: { schema: ToolUiSchema; form: Record<string, unknown>; run: ToolRun; events: ToolEvent[]; onArtifactDownload?: (artifact: ArtifactRef) => void; theme?: "dark" | "light" }) {
  const { language } = useLanguage();
  const document = { form, run };
  return <div className="tool-ui-results">{(schema.result_views ?? []).map((view) => {
    if (!conditionMatches(view.visible_when, document)) return null;
    const title = localized(view.title, language) || view.id;
    if (!supported.has(view.component)) return <div key={view.id} role="alert" className="tool-ui-error">Unsupported component: {view.component}</div>;
    const source = applyTransforms(resolvePointer(document, view.source), view.transforms);
    const content = hasResultData(view, source, run, events)
      ? <ResultContent view={view} value={source} run={run} events={events} title={title} theme={theme} onArtifactDownload={onArtifactDownload} />
      : null;
    return <section className="tool-ui-result" key={view.id} aria-labelledby={`${view.id}-title`}><h2 id={`${view.id}-title`}>{title}</h2>
      {content || <p className="tool-ui-empty">{localized(view.empty, language) || (language === "en" ? "No data" : "暂无数据")}</p>}
    </section>;
  })}</div>;
}
