import { useMemo, useState, type ComponentType, type MouseEvent } from "react";
import { Atom, CheckCircle2, ChevronDown, CircleEllipsis, CircleSlash, CircleX, Clock3, FilePenLine, FilePlus2, FileText, LoaderCircle, Plug, Puzzle, Search, ShieldQuestion, Terminal } from "lucide-react";
import type { MessagePart } from "../../api/types";
import { useLanguage } from "../../i18n/LanguageProvider";
import { activityStatus, activityTool, toolActivityKind, toolResultSummary, type ToolActivity, type ToolActivityKind, type ToolActivityStatus } from "./toolActivity";

export type ToolCall = Extract<MessagePart, { type: "tool_call" }>;
export type ToolCardProps = { part: ToolCall };
const cards = new Map<string, ComponentType<ToolCardProps>>();
export type ToolResult = Extract<MessagePart, { type: "tool_result" }>;
export type ToolResultCardProps = { part: ToolResult };
const resultCards = new Map<string, ComponentType<ToolResultCardProps>>();

export function registerToolRenderer(name: string, card: ComponentType<ToolCardProps>): void {
  cards.set(name, card);
}

export function registerToolResultRenderer(name: string, card: ComponentType<ToolResultCardProps>): void {
  resultCards.set(name, card);
}

export function ToolCallPart({ part }: ToolCardProps) {
  const Card = cards.get(part.tool);
  return Card ? <Card part={part} /> : <ToolActivityRow activity={{ key: "call", call: part, results: [] }} />;
}

export function ToolResultPart({ part }: ToolResultCardProps) {
  const Card = resultCards.get(part.tool);
  return Card ? <Card part={part} /> : <ToolActivityRow activity={{ key: "result", results: [part] }} />;
}

type Translate = ReturnType<typeof useLanguage>["t"];

export function activityLabel(activity: ToolActivity, t: Translate): string {
  if (!activity.call) return t("toolActivity.result");
  const kind = toolActivityKind(activity.call.tool);
  return kind === "unknown" ? activity.call.tool || t("toolActivity.unknownTool") : t(`toolActivity.action.${kind}`);
}

export function activitySummary(activity: ToolActivity, t: Translate): string {
  if (activity.call) return activity.call.summary.trim() ? activity.call.summary : t("toolActivity.noSummary");
  return activity.results[0] ? toolResultSummary(activity.results[0]) ?? t("toolActivity.structuredResult") : t("toolActivity.noSummary");
}

export function activityStatusLabel(activity: ToolActivity, t: Translate): string {
  const status = activityStatus(activity);
  const rawStatus = activity.call?.status.trim();
  return status === "unknown" && rawStatus
    ? t("toolActivity.unrecognizedStatus", { status: rawStatus })
    : t(`toolActivity.status.${status}`);
}

const kindIcons = { read: FileText, write: FilePlus2, edit: FilePenLine, bash: Terminal, search: Search, mcp: Plug, af3: Atom, unknown: Puzzle };

function ActivityKindIcon({ kind }: { kind: ToolActivityKind }) {
  const Icon = kindIcons[kind];
  return <Icon className="tool-activity-row__icon" size={15} aria-hidden="true" />;
}

export function ActivityStatusIcon({ status }: { status: ToolActivityStatus }) {
  const Icon = status === "completed" ? CheckCircle2
    : status === "failed" ? CircleX
    : status === "running" ? LoaderCircle
    : status === "approval_required" ? ShieldQuestion
    : status === "pending" ? Clock3
    : status === "cancelled" ? CircleSlash
    : CircleEllipsis;
  return <Icon className={`tool-activity-row__status-icon${status === "running" ? " message-spinner" : ""}`} size={13} aria-hidden="true" />;
}

function ToolResultDetails({ part, index, total }: { part: ToolResult; index: number; total: number }) {
  const { t } = useLanguage();
  const Card = resultCards.get(part.tool);
  const json = useMemo(() => Card ? undefined : JSON.stringify(part.result, null, 2), [Card, part.result]);
  return <section className="tool-activity-row__result">
    <div className="tool-activity-row__result-label">{total > 1 ? t("toolActivity.numberedResult", { index: index + 1, total }) : t("toolActivity.result")} · {part.tool}</div>
    {Card ? <Card part={part} /> : <>
      <p className="tool-activity-row__full-summary">{toolResultSummary(part) ?? t("toolActivity.structuredResult")}</p>
      <pre className="tool-activity-row__json" tabIndex={0} aria-label={t("toolActivity.resultJson", { tool: part.tool })}>{json}</pre>
    </>}
  </section>;
}

export function ToolActivityRow({ activity, visible = true, onUserToggle }: {
  activity: ToolActivity;
  visible?: boolean;
  onUserToggle?: (open: boolean) => void;
}) {
  const { t } = useLanguage();
  const status = activityStatus(activity);
  const [userOpen, setUserOpen] = useState<boolean | null>(null);
  const open = userOpen ?? false;
  const tool = activityTool(activity);
  const kind = toolActivityKind(tool);
  const summary = activitySummary(activity, t);
  const statusLabel = activityStatusLabel(activity, t);
  const CallCard = activity.call ? cards.get(activity.call.tool) : undefined;
  const toggle = (event: MouseEvent<HTMLElement>) => {
    // The native summary still supplies keyboard semantics; control the open state
    // here so automatic status changes never overwrite a deliberate choice.
    event.preventDefault();
    setUserOpen(!open);
    onUserToggle?.(!open);
  };

  return <details className="tool-activity-row" data-status={status} open={open}>
    <summary className="tool-activity-row__summary" onClick={toggle}>
      <ActivityKindIcon kind={kind} />
      <span className="tool-activity-row__content">
        <span className="tool-activity-row__label" title={tool}>{activityLabel(activity, t)}{(!activity.call || kind !== "unknown") && <span className="tool-activity-row__tool-name">{tool}</span>}</span>
        <span className="tool-activity-row__preview" title={summary}>{summary}</span>
      </span>
      <span className="tool-activity-row__status" title={statusLabel}><ActivityStatusIcon status={status} /><span>{statusLabel}</span></span>
      <ChevronDown className="tool-activity-row__chevron" size={13} aria-hidden="true" />
    </summary>
    {open && visible ? <div className="tool-activity-row__details">
      {activity.call && <p className="tool-activity-row__full-summary">{summary}</p>}
      {CallCard && activity.call ? <CallCard part={activity.call} /> : null}
      {activity.results.map((part, index) => <ToolResultDetails key={`${part.tool}:${index}`} part={part} index={index} total={activity.results.length} />)}
    </div> : null}
  </details>;
}
