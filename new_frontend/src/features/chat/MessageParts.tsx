import type { MessagePart } from "../../api/types";
import { hasMessagePartRenderer, RegisteredMessagePart, type MessageResourceActions } from "./messagePartRegistry";
import { ToolActivityGroup, type ToolActivityPart } from "./ToolActivityGroup";

function partKey(part: MessagePart, index: number): string {
  if (part.type === "file" || part.type === "artifact") return `${part.type}:${part.id}`;
  if (part.type === "tool_call" || part.type === "tool_result") return `${part.type}:${part.tool_call_id ?? part.tool}:${index}`;
  return `${part.type}:${index}`;
}

type PartGroup = { type: "part"; part: MessagePart; index: number }
  | { type: "tools"; parts: ToolActivityPart[]; key: string };

function groupParts(parts: MessagePart[]): PartGroup[] {
  const groups: PartGroup[] = [];
  const toolGroupKeys = new Map<string, number>();
  // Custom type renderers retain their original, per-part rendering contract.
  const groupTools = !hasMessagePartRenderer("tool_call") && !hasMessagePartRenderer("tool_result");
  parts.forEach((part, index) => {
    if (groupTools && (part.type === "tool_call" || part.type === "tool_result")) {
      const previous = groups.at(-1);
      if (previous?.type === "tools") {
        previous.parts.push(part);
      } else {
        const identity = part.tool_call_id || `${part.type}:${part.tool}:${index}`;
        const occurrence = toolGroupKeys.get(identity) ?? 0;
        toolGroupKeys.set(identity, occurrence + 1);
        groups.push({ type: "tools", parts: [part], key: `tools:${identity}:${occurrence}` });
      }
    } else {
      groups.push({ type: "part", part, index });
    }
  });
  return groups;
}

export function MessageParts({ parts, markdown = false, streaming = false, resourceActions }: {
  parts: MessagePart[];
  markdown?: boolean;
  streaming?: boolean;
  resourceActions?: MessageResourceActions;
}) {
  return <>{groupParts(parts).map((group) => group.type === "tools"
    ? <ToolActivityGroup key={group.key} parts={group.parts} />
    : <RegisteredMessagePart key={partKey(group.part, group.index)} part={group.part} markdown={markdown} streaming={streaming} resourceActions={resourceActions} />)}</>;
}
