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
export type BrowserDraftPolicy = { enabled: boolean; clearOnSignOut: boolean };
const policyKey = (userId: string) => `pskit.composer.policy.v1:${encodeURIComponent(userId)}`;
const draftPrefix = (userId: string) => `pskit.composer.v1:${encodeURIComponent(userId)}:`;
const volatileDrafts = new Map<string, ComposerDraft>();
const observers = new Map<string, Set<(draft: ComposerDraft) => void>>();

export function readBrowserDraftPolicy(userId: string): BrowserDraftPolicy {
  try {
    const saved = JSON.parse(window.localStorage.getItem(policyKey(userId)) ?? "null");
    if (saved?.version === 1) return { enabled: saved.enabled !== false, clearOnSignOut: saved.clearOnSignOut === true };
  } catch { /* Existing drafts remain intact if the preference cannot be read. */ }
  return { enabled: true, clearOnSignOut: false };
}

export function writeBrowserDraftPolicy(userId: string, policy: BrowserDraftPolicy): boolean {
  try {
    window.localStorage.setItem(policyKey(userId), JSON.stringify({ version: 1, enabled: policy.enabled, clearOnSignOut: policy.clearOnSignOut }));
    return true;
  } catch { return false; }
}

export function storedDraftScopes(userId: string): ComposerDraftScope[] {
  try {
    const prefix = draftPrefix(userId);
    const keys = new Set([...Object.keys(window.localStorage), ...volatileDrafts.keys()]);
    return [...keys].filter((key) => key.startsWith(prefix)).flatMap((key) => {
      try { return [{ userId, conversationId: decodeURIComponent(key.slice(prefix.length)) }]; }
      catch { return []; }
    });
  } catch { return []; }
}

export function clearComposerDraft(scope: ComposerDraftScope): void {
  const key = storageKey(scope);
  try { window.localStorage.removeItem(key); } catch { /* Memory is still cleared. */ }
  volatileDrafts.delete(key);
  observers.get(key)?.forEach((listener) => listener(emptyComposerDraft()));
}

export function clearUserComposerDrafts(userId: string): void {
  const prefix = draftPrefix(userId);
  const keys = new Set([...volatileDrafts.keys(), ...observers.keys()]);
  try { for (const key of Object.keys(window.localStorage)) keys.add(key); } catch { /* Clear accessible memory only. */ }
  for (const key of keys) if (key.startsWith(prefix)) {
    try { window.localStorage.removeItem(key); } catch { /* Memory is still cleared. */ }
    volatileDrafts.delete(key);
    observers.get(key)?.forEach((listener) => listener(emptyComposerDraft()));
  }
}

export function handleComposerSignOut(userId: string): void {
  if (readBrowserDraftPolicy(userId).clearOnSignOut) clearUserComposerDrafts(userId);
  for (const key of volatileDrafts.keys()) if (key.startsWith(draftPrefix(userId))) volatileDrafts.delete(key);
}

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
  if (!readBrowserDraftPolicy(scope.userId).enabled) {
    const working = volatileDrafts.get(storageKey(scope));
    if (working) return working;
  }
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
  const sanitized = {
    content: draft.content, model: draft.model, reasoning_effort: draft.reasoning_effort,
    attachments: refs(draft.attachments), skills: refs(draft.skills), resources: refs(draft.resources),
  };
  const retain = readBrowserDraftPolicy(scope.userId).enabled;
  if (!retain) { volatileDrafts.set(storageKey(scope), sanitized); persisted = true; }
  try {
    if (retain) window.localStorage.setItem(storageKey(scope), JSON.stringify({
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
