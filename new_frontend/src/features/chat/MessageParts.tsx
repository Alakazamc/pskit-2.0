import { FileText, LoaderCircle } from "lucide-react";
import type { MessagePart } from "../../api/types";
import { ToolCallPart } from "./toolRegistry";
import { LazyMarkdownContent } from "./LazyMarkdownContent";

export function MessageParts({ parts, markdown = false }: { parts: MessagePart[]; markdown?: boolean }) {
  return <>{parts.map((part, index) => {
    switch (part.type) {
      case "text": return markdown
        ? <LazyMarkdownContent key={index} text={part.text} />
        : <p key={index} className="message-text">{part.text}</p>;
      case "tool_call": return <ToolCallPart key={index} part={part} />;
      case "tool_result": return <div key={index} className="tool-result-inline"><strong>{part.tool}</strong><pre>{JSON.stringify(part.result, null, 2)}</pre></div>;
      case "file": return <div key={index} className="artifact-inline"><FileText size={16} />{part.name}</div>;
      case "artifact": return <div key={index} className="artifact-inline"><FileText size={16} />{part.name}</div>;
      case "citation": {
        const safeUrl = /^https?:\/\//i.test(part.url);
        return safeUrl
          ? <a key={index} href={part.url} target="_blank" rel="noopener noreferrer">{part.title}</a>
          : <span key={index}>{part.title}</span>;
      }
      case "progress": return <div key={index} className="progress-inline" role="status" aria-label={part.label}><LoaderCircle className="message-spinner" size={16} aria-hidden="true" />{part.label}</div>;
      case "error": return <div key={index} className="message-part-error" role="alert">{part.message}</div>;
    }
  })}</>;
}
