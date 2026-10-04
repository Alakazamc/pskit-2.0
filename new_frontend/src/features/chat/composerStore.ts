import { create } from "zustand";
import type { ContextRef, MessageRequest } from "../../api/types";

type ComposerState = MessageRequest & {
  setModel: (model: string | undefined) => void;
  setReasoningEffort: (effort: MessageRequest["reasoning_effort"]) => void;
  addSkill: (ref: ContextRef) => void;
  addResource: (ref: ContextRef) => void;
  addAttachment: (ref: ContextRef) => void;
  removeRef: (kind: "skills" | "resources" | "attachments", id: string) => void;
  setText: (text: string) => void;
  clear: () => void;
  clearAfterSend: () => void;
};
const empty = { content: "", attachments: [], skills: [], resources: [], model: undefined, reasoning_effort: undefined };
export const useComposerStore = create<ComposerState>((set) => ({
  ...empty,
  setModel: (model) => set({ model, reasoning_effort: undefined }),
  setReasoningEffort: (reasoning_effort) => set({ reasoning_effort }),
  setText: (content) => set({ content }),
  addSkill: (ref) => set((state) => ({ skills: [...state.skills.filter((item) => item.id !== ref.id), ref] })),
  addResource: (ref) => set((state) => ({ resources: [...state.resources.filter((item) => item.id !== ref.id), ref] })),
  addAttachment: (ref) => set((state) => ({ attachments: [...state.attachments.filter((item) => item.id !== ref.id), ref] })),
  removeRef: (kind, id) => set((state) => ({ [kind]: state[kind].filter((item) => item.id !== id) })),
  clear: () => set(empty),
  clearAfterSend: () => set({ content: "", attachments: [], skills: [], resources: [] }),
}));
