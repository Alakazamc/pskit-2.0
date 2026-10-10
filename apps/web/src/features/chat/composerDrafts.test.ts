import { afterEach, describe, expect, it } from "vitest";
import { clearComposerDraft, clearUserComposerDrafts, emptyComposerDraft, handleComposerSignOut, readBrowserDraftPolicy, readComposerDraft, storedDraftScopes, writeBrowserDraftPolicy, writeComposerDraft } from "./composerDrafts";

afterEach(() => { clearUserComposerDrafts("draft-test-a"); clearUserComposerDrafts("draft-test-b"); localStorage.clear(); });
const a = { userId: "draft-test-a", conversationId: "session:first" };
const second = { ...a, conversationId: "session:second" };
const b = { ...a, userId: "draft-test-b" };
const draft = { ...emptyComposerDraft(), content: "private draft", model: "complete/provider/alias", attachments: [{ id: "file-id", name: "image.png" }] };

describe("browser draft privacy", () => {
  it("retains existing v1 drafts by default and stores only safe reference fields", () => {
    writeComposerDraft(a, { ...draft, attachments: [{ id: "file-id", name: "image.png", blob: "blob:private", api_key: "secret" } as unknown as typeof draft.attachments[number]] });
    expect(readBrowserDraftPolicy(a.userId)).toEqual({ enabled: true, clearOnSignOut: false });
    expect(readComposerDraft(a)).toEqual(draft);
    const saved = localStorage.getItem(Object.keys(localStorage)[0])!;
    expect(saved).not.toContain("blob:"); expect(saved).not.toContain("secret");
  });
  it("uses page memory on opt out and never silently deletes or overwrites existing stored drafts", () => {
    writeComposerDraft(a, draft);
    const previous = localStorage.getItem("pskit.composer.v1:draft-test-a:session%3Afirst");
    writeBrowserDraftPolicy(a.userId, { enabled: false, clearOnSignOut: false });
    writeComposerDraft(a, { ...draft, content: "memory only" });
    expect(readComposerDraft(a).content).toBe("memory only");
    expect(localStorage.getItem("pskit.composer.v1:draft-test-a:session%3Afirst")).toBe(previous);
    expect(readComposerDraft(b).content).toBe("");
  });
  it("clears only the selected conversation or selected user's drafts, leaving auth and other accounts intact", () => {
    for (const scope of [a, second, b]) writeComposerDraft(scope, draft);
    localStorage.setItem("research_access_token", "unrelated-auth-test");
    clearComposerDraft(a);
    expect(readComposerDraft(a).content).toBe(""); expect(readComposerDraft(second).content).toBe("private draft");
    clearUserComposerDrafts(a.userId);
    expect(storedDraftScopes(a.userId)).toEqual([]); expect(readComposerDraft(b).content).toBe("private draft");
    expect(localStorage.getItem("research_access_token")).toBe("unrelated-auth-test");
  });
  it("only clears persistent drafts on sign out when the user opted in", () => {
    writeComposerDraft(a, draft); handleComposerSignOut(a.userId);
    expect(readComposerDraft(a).content).toBe("private draft");
    writeBrowserDraftPolicy(a.userId, { enabled: true, clearOnSignOut: true });
    handleComposerSignOut(a.userId); expect(readComposerDraft(a).content).toBe("");
  });
});
