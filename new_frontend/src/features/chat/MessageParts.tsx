import type { MessagePart } from "../../api/types";
import { RegisteredMessagePart, type MessageResourceActions } from "./messagePartRegistry";

function partKey(part: MessagePart, index: number): string {
  if (part.type === "file" || part.type === "artifact") return `${part.type}:${part.id}`;
  if (part.type === "tool_call" || part.type === "tool_result") return `${part.type}:${part.tool_call_id ?? part.tool}:${index}`;
  return `${part.type}:${index}`;
}

export function MessageParts({ parts, markdown = false, streaming = false, resourceActions }: {
  parts: MessagePart[];
  markdown?: boolean;
  streaming?: boolean;
  resourceActions?: MessageResourceActions;
}) {
  return <>{parts.map((part, index) => <RegisteredMessagePart key={partKey(part, index)} part={part} markdown={markdown} streaming={streaming} resourceActions={resourceActions} />)}</>;
}
