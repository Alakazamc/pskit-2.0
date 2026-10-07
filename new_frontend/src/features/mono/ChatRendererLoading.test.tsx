import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "../../app/App";

const renderer = vi.hoisted(() => {
  let ready!: () => void;
  const promise = new Promise<void>((resolve) => { ready = resolve; });
  return { promise, ready };
});
vi.mock("../chat/MarkdownContent", async () => {
  await renderer.promise;
  return { MarkdownContent: ({ text }: { text: string }) => <div data-testid="formatted-history">{text}</div> };
});

afterEach(() => {
  cleanup(); vi.unstubAllGlobals(); localStorage.clear();
  window.history.replaceState(null, "", "/");
});

it("keeps history hidden until its Markdown chunk is ready instead of flashing raw text", async () => {
  localStorage.setItem("research_access_token", "demo-token");
  localStorage.setItem("research_language", "en");
  window.history.replaceState(null, "", "/session/history");
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const path = new URL(String(input), "http://test").pathname;
    if (path.endsWith("/me")) return Response.json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (path.endsWith("/c")) return Response.json([{ id: "history", project_id: "project-alice", title: "History", status: "completed" }]);
    if (path.endsWith("/messages")) return Response.json([{ id: "reply", session_id: "history", role: "assistant", created_at: "2026-10-07T00:00:00Z", parts: [{ type: "text", text: "**Historical reply**" }] }]);
    return Response.json([]);
  });
  vi.stubGlobal("fetch", fetcher);
  render(<App />);
  await waitFor(() => expect(fetcher.mock.calls.some(([url]) => String(url).endsWith("/messages"))).toBe(true));
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 40)); });
  expect(screen.queryByText("**Historical reply**")).not.toBeInTheDocument();
  expect(screen.getByRole("main").querySelector(".page-loading-spinner")).toBeInTheDocument();
  await act(async () => { renderer.ready(); });
  expect(await screen.findByTestId("formatted-history")).toHaveTextContent("**Historical reply**");
  expect(screen.getByRole("main").querySelector(".page-loading")).not.toBeInTheDocument();
});
