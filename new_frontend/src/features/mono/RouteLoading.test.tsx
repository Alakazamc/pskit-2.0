import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { App } from "../../app/App";
import { useComposerStore } from "../chat/composerStore";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

const json = (value: unknown, status = 200) => Response.json(value, { status });
const user = { id: "alice", name: "Alice", email: "alice@example.org" };
const sessions = [
  { id: "first", project_id: "project-alice", title: "First chat", status: "completed", latest_run_id: "old-run" },
  { id: "second", project_id: "project-alice", title: "Second chat", status: "completed", latest_run_id: "second-old-run" },
];
const history = (sessionId: string, text: string) => [
  { id: `user-${sessionId}`, session_id: sessionId, role: "user", created_at: "2026-10-07T00:00:00Z", parts: [{ type: "text", text: "An earlier question" }] },
  { id: `assistant-${sessionId}`, session_id: sessionId, role: "assistant", created_at: "2026-10-07T00:00:01Z", parts: [{ type: "text", text }] },
];
const originalScrollHeight = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollHeight");

beforeEach(() => {
  localStorage.setItem("research_access_token", "demo-token");
  localStorage.setItem("research_language", "en");
});
afterEach(() => {
  cleanup(); useComposerStore.getState().clear();
  vi.unstubAllGlobals(); vi.restoreAllMocks(); localStorage.clear();
  window.history.replaceState(null, "", "/");
  if (originalScrollHeight) Object.defineProperty(HTMLElement.prototype, "scrollHeight", originalScrollHeight);
});

it("shows one shared loader until both tool catalogs settle, without flashing the built-in card", async () => {
  window.history.replaceState(null, "", "/tools");
  const tools = deferred<Response>();
  const products = deferred<Response>();
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = new URL(String(input), "http://test").pathname;
    if (path.endsWith("/me")) return json(user);
    if (path.endsWith("/mcp/tools")) return tools.promise;
    if (path.endsWith("/tool-products")) return products.promise;
    return json([]);
  }));
  render(<App />);
  await screen.findByRole("searchbox", { name: "Search tools" });
  const main = within(screen.getByRole("main"));
  expect(main.queryByRole("link", { name: "Structure viewer" })).not.toBeInTheDocument();
  expect(main.getAllByRole("status")).toHaveLength(1);
  expect(screen.getByRole("main").querySelector(".page-loading-spinner")).toBeInTheDocument();
  await act(async () => { tools.resolve(json([{ name: "search_pdb", description: "Search structures", input_schema: {} }])); });
  expect(main.queryByRole("link", { name: "search_pdb" })).not.toBeInTheDocument();
  await act(async () => { products.resolve(json({ items: [], next_cursor: null })); });
  expect(await main.findByRole("link", { name: "Structure viewer" })).toBeInTheDocument();
  expect(main.getByRole("link", { name: "search_pdb" })).toBeInTheDocument();
  expect(main.queryByRole("status")).not.toBeInTheDocument();
});

it("keeps available tools visible and offers retry after a partial catalog failure", async () => {
  window.history.replaceState(null, "", "/tools");
  const retry = deferred<Response>();
  let attempts = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = new URL(String(input), "http://test").pathname;
    if (path.endsWith("/me")) return json(user);
    if (path.endsWith("/mcp/tools")) return ++attempts === 1 ? json({ detail: "Unavailable" }, 503) : retry.promise;
    if (path.endsWith("/tool-products")) return json({ items: [], next_cursor: null });
    return json([]);
  }));
  render(<App />);
  expect(await screen.findByRole("link", { name: "Structure viewer" })).toBeInTheDocument();
  const actor = userEvent.setup();
  await actor.click(screen.getByRole("button", { name: "Retry" }));
  expect(screen.getByRole("link", { name: "Structure viewer" })).toBeInTheDocument();
  await act(async () => { retry.resolve(json([{ name: "search_pdb", description: "Search structures", input_schema: {} }])); });
  expect(await screen.findByRole("link", { name: "search_pdb" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
});

it("waits for the selected history before revealing the composer and never replays completed runs", async () => {
  window.history.replaceState(null, "", "/session/first");
  const second = deferred<Response>();
  const requested: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = new URL(String(input), "http://test").pathname;
    requested.push(path);
    if (path.endsWith("/me")) return json(user);
    if (path.endsWith("/c")) return json(sessions);
    if (path.endsWith("/first/messages")) return json(history("first", "First historical answer"));
    if (path.endsWith("/second/messages")) return second.promise;
    if (path.includes("/runs/")) return new Response("", { headers: { "content-type": "text/event-stream" } });
    return json([]);
  }));
  render(<App />);
  await screen.findByText("First historical answer");
  const actor = userEvent.setup();
  await actor.click(screen.getByRole("link", { name: "Second chat" }));
  await waitFor(() => expect(requested).toContain("/api/v1/c/second/messages"));
  const main = within(screen.getByRole("main"));
  expect(main.queryByText("First historical answer")).not.toBeInTheDocument();
  expect(main.queryByText("What would you like to explore?")).not.toBeInTheDocument();
  expect(main.queryByRole("textbox", { name: "Message" })).not.toBeInTheDocument();
  expect(main.getAllByRole("status")).toHaveLength(1);
  expect(screen.getByRole("main").querySelector(".page-loading-spinner")).toBeInTheDocument();
  await act(async () => { second.resolve(json(history("second", "Second historical answer"))); });
  expect(await main.findByText("Second historical answer")).toBeInTheDocument();
  expect(main.getByRole("textbox", { name: "Message" })).toBeInTheDocument();
  expect(requested.filter((path) => path.includes("/runs/"))).toEqual([]);
  await actor.click(screen.getByRole("link", { name: "First chat" }));
  expect(main.getByText("First historical answer")).toBeInTheDocument();
  expect(screen.getByRole("main").querySelector(".page-loading")).not.toBeInTheDocument();
});

it("replaces a failed history load with retry instead of an empty conversation", async () => {
  window.history.replaceState(null, "", "/session/first");
  let attempts = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = new URL(String(input), "http://test").pathname;
    if (path.endsWith("/me")) return json(user);
    if (path.endsWith("/c")) return json(sessions);
    if (path.endsWith("/first/messages")) return ++attempts === 1
      ? json({ detail: "Temporarily unavailable" }, 503) : json(history("first", "Recovered historical answer"));
    return json([]);
  }));
  render(<App />);
  const retry = await screen.findByRole("button", { name: "Retry" });
  expect(screen.queryByRole("textbox", { name: "Message" })).not.toBeInTheDocument();
  expect(screen.getByRole("main").querySelector(".page-loading")).not.toBeInTheDocument();
  await userEvent.setup().click(retry);
  expect(await screen.findByText("Recovered historical answer")).toBeInTheDocument();
});

it("positions each cached conversation independently before showing its history", async () => {
  window.history.replaceState(null, "", "/session/first");
  Object.defineProperty(HTMLElement.prototype, "scrollHeight", { configurable: true, get() { return this.classList.contains("conversation-scroll") ? 4000 : 0; } });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = new URL(String(input), "http://test").pathname;
    if (path.endsWith("/me")) return json(user);
    if (path.endsWith("/c")) return json(sessions);
    if (path.endsWith("/first/messages")) return json(history("first", "First historical answer"));
    if (path.endsWith("/second/messages")) return json(history("second", "Second historical answer"));
    return json([]);
  }));
  render(<App />);
  const actor = userEvent.setup();
  await screen.findByText("First historical answer");
  await actor.click(screen.getByRole("link", { name: "Second chat" }));
  await screen.findByText("Second historical answer");
  await actor.click(screen.getByRole("link", { name: "First chat" }));
  const first = document.querySelector<HTMLElement>(".conversation-scroll")!;
  fireEvent.wheel(first, { deltaY: -30 });
  first.scrollTop = 200;
  await actor.click(screen.getByRole("link", { name: "Second chat" }));
  expect(document.querySelector<HTMLElement>(".conversation-scroll")!.scrollTop).toBe(4000);
});

it("loads failed run details as one snapshot before displaying the historical conversation", async () => {
  window.history.replaceState(null, "", "/session/first");
  const events = deferred<Response>();
  const requests: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://test");
    if (url.pathname.endsWith("/me")) return json(user);
    if (url.pathname.endsWith("/c")) return json([{ ...sessions[0], status: "failed", latest_run_id: "failed-run" }]);
    if (url.pathname.endsWith("/first/messages")) return json([history("first", "unused")[0]]);
    if (url.pathname.endsWith("/failed-run/events")) { requests.push(url.search); return events.promise; }
    return json([]);
  }));
  render(<App />);
  await waitFor(() => expect(requests).toEqual(["?follow=false"]));
  expect(screen.queryByRole("textbox", { name: "Message" })).not.toBeInTheDocument();
  await act(async () => { events.resolve(new Response(`data: ${JSON.stringify({ id: "1", run_id: "failed-run", type: "run.failed", data: { code: "REMOTE_ERROR", message: "Saved failure details" } })}\n\n`, { headers: { "content-type": "text/event-stream" } })); });
  expect(await screen.findByText("Saved failure details")).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Message" })).toBeInTheDocument();
});

it("preserves cancelled partial replies and retries a failed historical snapshot", async () => {
  window.history.replaceState(null, "", "/session/first");
  let attempts = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://test");
    if (url.pathname.endsWith("/me")) return json(user);
    if (url.pathname.endsWith("/c")) return json([{ ...sessions[0], status: "cancelled", latest_run_id: "cancelled-run" }]);
    if (url.pathname.endsWith("/first/messages")) return json([history("first", "unused")[0]]);
    if (url.pathname.endsWith("/cancelled-run/events")) {
      expect(url.search).toBe("?follow=false");
      if (++attempts === 1) return json({ detail: "Unavailable" }, 503);
      const events = [
        { id: "1", run_id: "cancelled-run", type: "message.delta", data: { delta: "Saved partial reply" } },
        { id: "2", run_id: "cancelled-run", type: "run.cancelled", data: {} },
      ];
      return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(""), { headers: { "content-type": "text/event-stream" } });
    }
    return json([]);
  }));
  render(<App />);
  await userEvent.setup().click(await screen.findByRole("button", { name: "Retry" }));
  expect(await screen.findByText("Saved partial reply")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Cancel run" })).not.toBeInTheDocument();
  expect(attempts).toBe(2);
});

it("uses the same quiet loader inside a tool's run history", async () => {
  window.history.replaceState(null, "", "/tools/run/fetch_uniprot?tab=history");
  const runs = deferred<Response>();
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://test");
    if (url.pathname.endsWith("/me")) return json(user);
    if (url.pathname.endsWith("/mcp/tools")) return json([{ name: "fetch_uniprot", description: "Read proteins", input_schema: {} }]);
    if (url.pathname.endsWith("/tool-runs")) return runs.promise;
    return json([]);
  }));
  render(<App />);
  const status = await screen.findByRole("status", { name: "Loading runs…" });
  expect(status.querySelector(".page-loading-spinner")).toBeInTheDocument();
  expect(status).toHaveTextContent("");
  await act(async () => { runs.resolve(json([])); });
  await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());
});
