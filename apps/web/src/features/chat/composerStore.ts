import { create } from "zustand";
import type { ContextRef, MessageRequest } from "../../api/types";
import { draftAfterSubmission, emptyComposerDraft, readComposerDraft, writeComposerDraft, type ComposerDraftScope } from "./composerDrafts";

type ComposerState = MessageRequest & {
  setModel: (model: string | undefined) => void;
  setReasoningEffort: (effort: MessageRequest["reasoning_effort"]) => void;
  addSkill: (ref: ContextRef) => void;
  addResource: (ref: ContextRef) => void;
  addAttachment: (ref: ContextRef) => void;
  removeRef: (kind: "skills" | "resources" | "attachments", id: string) => void;
  setText: (text: string) => void;
  clear: () => void;
  clearAfterSend: (submitted?: MessageRequest) => void;
};
export function createComposerStore(scope?: ComposerDraftScope) {
  return create<ComposerState>((set, get) => {
    let persisted = true;
    const current = () => scope && persisted ? readComposerDraft(scope, get()) : get();
    const update = (patch: Partial<MessageRequest>) => {
      set({ ...current(), ...patch });
      if (scope) persisted = writeComposerDraft(scope, get());
    };
    const add = (kind: "skills" | "resources" | "attachments", ref: ContextRef) =>
      update({ [kind]: [...current()[kind].filter((item) => item.id !== ref.id), ref] });
    return {
      ...(scope ? readComposerDraft(scope) : emptyComposerDraft()),
      setModel: (model) => update({ model, reasoning_effort: undefined }),
      setReasoningEffort: (reasoning_effort) => update({ reasoning_effort }),
      setText: (content) => update({ content }),
      addSkill: (ref) => add("skills", ref),
      addResource: (ref) => add("resources", ref),
      addAttachment: (ref) => add("attachments", ref),
      removeRef: (kind, id) => update({ [kind]: current()[kind].filter((item) => item.id !== id) }),
      clear: () => update(emptyComposerDraft()),
      clearAfterSend: (submitted = get()) => update(draftAfterSubmission(current(), submitted)),
    };
  });
}

// Compatibility for standalone/unscoped consumers; routed chats always use their own store.
export const useComposerStore = createComposerStore();
