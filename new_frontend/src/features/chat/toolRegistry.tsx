import type { ComponentType } from "react";
import { CheckCircle2, Terminal } from "lucide-react";
import type { MessagePart } from "../../api/types";

export type ToolCall = Extract<MessagePart, { type: "tool_call" }>;
export type ToolCardProps = { part: ToolCall };
const cards = new Map<string, ComponentType<ToolCardProps>>();

export function registerToolRenderer(name: string, card: ComponentType<ToolCardProps>): void {
  cards.set(name, card);
}

function GenericToolCard({ part }: ToolCardProps) {
  return <div className="tool-card"><Terminal size={16} /><div><b>{part.tool}</b><span>{part.summary}</span></div><CheckCircle2 size={16} /></div>;
}

export function ToolCallPart({ part }: ToolCardProps) {
  const Card = cards.get(part.tool) ?? GenericToolCard;
  return <Card part={part} />;
}
