import type { ContextRef, MessageRequest } from "../../api/types";

export type ComposerDraftScope = { userId: string; conversationId: string };
export type ComposerDraft = MessageRequest;

export const conversationDraftScope = (userId: string, sessionId: string | null, projectId: string | null = null): ComposerDraftScope => ({
  userId, conversationId: sessionId ? `session:${sessionId}` : projectId ? `new:project:${projectId}` : "new:personal",
});

export const emptyComposerDraft = (): ComposerDraft => ({
  content: "", attachments: [], skills: [], resources: [], model: undefined, reasoning_effort: undefined,
});

const efforts = new Set(["off", "minimal", "low", "medium", "high", "xhigh", "max"]);
const storageKey = (scope: ComposerDraftScope) =>
  `pskit.composer.v1:${encodeURIComponent(scope.userId)}:${encodeURIComponent(scope.conversationId)}`;
const observers = new Map<string, Set<(draft: ComposerDraft) => void>>();

export function observeComposerDraft(scope: ComposerDraftScope, listener: (draft: ComposerDraft) => void): () => void {
  const key = storageKey(scope);
  const listeners = observers.get(key) ?? new Set();
  listeners.add(listener);
  observers.set(key, listeners);
  return () => { listeners.delete(listener); if (!listeners.size) observers.delete(key); };
}

function refs(value: unknown): ContextRef[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is ContextRef => item !== null && typeof item === "object"
    && typeof item.id === "string" && typeof item.name === "string")
    .map(({ id, name }) => ({ id, name }));
}

// Store only the draft and ready context IDs, never files, credentials, or blob URLs.
export function readComposerDraft(scope: ComposerDraftScope, fallback = emptyComposerDraft()): ComposerDraft {
  try {
    const raw = window.localStorage.getItem(storageKey(scope));
    const saved: unknown = raw ? JSON.parse(raw) : null;
    if (!saved || typeof saved !== "object" || !("version" in saved) || saved.version !== 1) return emptyComposerDraft();
    const value = saved as Record<string, unknown>;
    return {
      content: typeof value.content === "string" ? value.content : "",
      model: typeof value.model === "string" && value.model ? value.model : undefined,
      reasoning_effort: typeof value.reasoning_effort === "string" && efforts.has(value.reasoning_effort)
        ? value.reasoning_effort as ComposerDraft["reasoning_effort"] : undefined,
      attachments: refs(value.attachments), skills: refs(value.skills), resources: refs(value.resources),
    };
  } catch { return fallback; }
}

export function writeComposerDraft(scope: ComposerDraftScope, draft: ComposerDraft): boolean {
  let persisted = false;
  try {
    window.localStorage.setItem(storageKey(scope), JSON.stringify({
      version: 1, content: draft.content, model: draft.model, reasoning_effort: draft.reasoning_effort,
      attachments: refs(draft.attachments), skills: refs(draft.skills), resources: refs(draft.resources),
    }));
    persisted = true;
  } catch { /* Keep the working in-memory composer if browser storage is unavailable. */ }
  observers.get(storageKey(scope))?.forEach((listener) => listener({
    content: draft.content, model: draft.model, reasoning_effort: draft.reasoning_effort,
    attachments: refs(draft.attachments), skills: refs(draft.skills), resources: refs(draft.resources),
  }));
  return persisted;
}

export function draftAfterSubmission(current: ComposerDraft, submitted: ComposerDraft): ComposerDraft {
  const remaining = (kind: "attachments" | "skills" | "resources") => {
    const sentIds = new Set(submitted[kind].map((ref) => ref.id));
    return current[kind].filter((ref) => !sentIds.has(ref.id));
  };
  return {
    ...current, content: current.content === submitted.content ? "" : current.content,
    attachments: remaining("attachments"), skills: remaining("skills"), resources: remaining("resources"),
  };
}

export function moveComposerDraft(source: ComposerDraftScope, destination: ComposerDraftScope): void {
  const draft = readComposerDraft(source);
  writeComposerDraft(destination, draft);
  writeComposerDraft(source, { ...emptyComposerDraft(), model: draft.model, reasoning_effort: draft.reasoning_effort });
}

export function completeComposerDraft(scope: ComposerDraftScope, submitted: ComposerDraft): void {
  writeComposerDraft(scope, draftAfterSubmission(readComposerDraft(scope), submitted));
}
