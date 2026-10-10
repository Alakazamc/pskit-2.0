import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it } from "vitest";
import { LanguageProvider } from "../../i18n/LanguageProvider";
import { BrowserDraftSettings } from "./BrowserDraftSettings";
import { emptyComposerDraft, readComposerDraft, writeComposerDraft } from "../chat/composerDrafts";

afterEach(() => { cleanup(); localStorage.clear(); });
it("explains retention and exposes explicit account and conversation cleanup controls", async () => {
  const client = new QueryClient();
  client.setQueryData(["sessions", "alice", "project-alice"], [{ id: "a", title: "Protein analysis" }, { id: "b", title: "Another research draft" }]);
  const a = { userId: "alice", conversationId: "session:a" };
  const b = { userId: "alice", conversationId: "session:b" };
  const other = { userId: "bob", conversationId: "session:a" };
  for (const scope of [a, b, other]) writeComposerDraft(scope, { ...emptyComposerDraft(), content: "Not sent" });
  render(<QueryClientProvider client={client}><LanguageProvider><BrowserDraftSettings userId="alice" /></LanguageProvider></QueryClientProvider>);
  const actor = userEvent.setup();
  expect(screen.getByText(/按账号与对话隔离/)).toBeInTheDocument();
  await actor.click(screen.getByRole("checkbox", { name: "在此浏览器保留草稿" }));
  expect(readComposerDraft(a).content).toBe("Not sent");
  await actor.selectOptions(screen.getByLabelText("对话草稿"), "session:a");
  await actor.click(screen.getByRole("button", { name: "清理选中草稿" }));
  expect(readComposerDraft(a).content).toBe(""); expect(readComposerDraft(b).content).toBe("Not sent");
  await actor.click(screen.getByRole("button", { name: "清理此账号全部草稿" }));
  expect(readComposerDraft(b).content).toBe(""); expect(readComposerDraft(other).content).toBe("Not sent");
});
