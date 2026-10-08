import type { ComponentType } from "react";
import { CheckCircle2, ChevronDown, CircleEllipsis, CircleX, LoaderCircle, Terminal } from "lucide-react";
import type { MessagePart } from "../../api/types";

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

function GenericToolCard({ part }: ToolCardProps) {
  const statusIcon = part.status === "completed" ? <CheckCircle2 size={16} aria-hidden="true" />
    : part.status === "failed" ? <CircleX size={16} aria-hidden="true" />
    : part.status === "running" ? <LoaderCircle className="message-spinner" size={16} aria-hidden="true" />
    : <CircleEllipsis size={16} aria-hidden="true" />;
  return <div className={`tool-card ${part.status}`}><Terminal size={16} aria-hidden="true" /><div><b>{part.tool}</b><span>{part.summary}</span></div>{statusIcon}</div>;
}

export function ToolCallPart({ part }: ToolCardProps) {
  const Card = cards.get(part.tool) ?? GenericToolCard;
  return <Card part={part} />;
}

function resultSummary(part: ToolResult): string {
  for (const key of ["summary", "message", "status", "title"]) {
    const value = part.result[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return "Structured result";
}

function GenericToolResultCard({ part }: ToolResultCardProps) {
  return <details className="tool-result-card">
    <summary><Terminal size={16} aria-hidden="true" /><span><strong>{part.tool}</strong><small>{resultSummary(part)}</small></span><ChevronDown size={15} aria-hidden="true" /></summary>
    <pre>{JSON.stringify(part.result, null, 2)}</pre>
  </details>;
}

export function ToolResultPart({ part }: ToolResultCardProps) {
  const Card = resultCards.get(part.tool) ?? GenericToolResultCard;
  return <Card part={part} />;
}
