import type { ComponentType } from "react";
import { AlertTriangle, ExternalLink, LoaderCircle, Puzzle } from "lucide-react";
import type { MessagePart } from "../../api/types";
import { LazyMarkdownContent } from "./LazyMarkdownContent";
import { ResourceCard, type MessageResourceActions } from "./ResourceCard";
import { ToolCallPart, ToolResultPart } from "./toolRegistry";

export type { MessageResourceActions } from "./ResourceCard";

export type MessagePartRendererProps = {
  part: MessagePart;
  markdown: boolean;
  streaming: boolean;
  resourceActions?: MessageResourceActions;
};

const renderers = new Map<string, ComponentType<MessagePartRendererProps>>();

export function registerMessagePartRenderer(type: string, renderer: ComponentType<MessagePartRendererProps>): void {
  renderers.set(type, renderer);
}

function BuiltInMessagePart({ part, markdown, streaming, resourceActions }: MessagePartRendererProps) {
  switch (part.type) {
    case "text": return markdown
      ? <LazyMarkdownContent text={part.text} streaming={streaming} />
      : <p className="message-text">{part.text}</p>;
    case "tool_call": return <ToolCallPart part={part} />;
    case "tool_result": return <ToolResultPart part={part} />;
    case "file":
    case "artifact": return <ResourceCard part={part} actions={resourceActions} />;
    case "citation": {
      const safeUrl = /^https?:\/\//i.test(part.url);
      return safeUrl
        ? <a className="message-citation" href={part.url} target="_blank" rel="noopener noreferrer"><span>{part.title}</span><ExternalLink size={13} aria-hidden="true" /></a>
        : <span className="message-citation"><span>{part.title}</span></span>;
    }
    case "progress": return <div className="progress-inline" role="progressbar" aria-label={part.label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={part.value}>
      <LoaderCircle className="message-spinner" size={16} aria-hidden="true" /><span>{part.label}</span><progress max={100} value={part.value} />
    </div>;
    case "error": return <div className="message-part-error" role="alert"><AlertTriangle size={16} aria-hidden="true" /><span>{part.message}</span></div>;
    default: {
      const unknown = part as { type?: string };
      return <div className="message-part-unknown"><Puzzle size={16} aria-hidden="true" /><span>{unknown.type || "unknown"}</span></div>;
    }
  }
}

export function RegisteredMessagePart(props: MessagePartRendererProps) {
  const Renderer = renderers.get(props.part.type) ?? BuiltInMessagePart;
  return <Renderer {...props} />;
}

