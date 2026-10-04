import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "../../app/App";
import { useComposerStore } from "../chat/composerStore";

afterEach(() => {
  cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); localStorage.clear();
  useComposerStore.getState().clear(); window.history.replaceState(null, "", "/");
});

it.each([
  [null, false], ["project-lab", false], [null, true],
] as const)("refreshes naming after completion and stops on success or network error, project=%s error=%s", async (projectId, networkError) => {
  localStorage.setItem("research_access_token", "demo-token");
  window.history.replaceState(null, "", projectId ? `/p/${projectId}/new` : "/");
  const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });
  const target = projectId ? "/api/v1/g/g-p-lab/c" : "/api/v1/c";
  let created: { title?: string; auto_title?: boolean } | undefined;
  let sent = false, named = false, listReads = 0;
  const session = () => ({
    id: "session-first", project_id: projectId ?? "project-alice", title: named ? "蛋白质结构分析" : "帮我分析蛋白质结构",
    title_status: named ? "generated" : sent ? "pending" : "idle",
    status: sent ? "completed" : "idle", latest_run_id: sent ? "run-first" : null,
  });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (path.endsWith("/g")) return json([
      { id: "project-alice", name: "Personal", description: "" },
      { id: "project-lab", name: "Lab", description: "" },
    ]);
    if (path === target && init?.method === "POST") { created = JSON.parse(String(init.body)); return json(session()); }
    if (path === target) {
      listReads += 1;
      if (networkError && named) return new Response("Unavailable", { status: 503 });
      return json(created ? [session()] : []);
    }
    if (path.endsWith("/c/session-first/messages") && init?.method === "POST") { sent = true; return json({ run_id: "run-first" }); }
    if (path.includes("/runs/run-first/events")) return new Response(
      `data: ${JSON.stringify({ id: "1", run_id: "run-first", type: "run.completed", data: {} })}\n\n`,
      { headers: { "content-type": "text/event-stream" } },
    );
    if (path.endsWith("/c/session-first/messages")) return json(sent ? [
      { id: "user-first", session_id: "session-first", role: "user", parts: [{ type: "text", text: "帮我分析蛋白质结构" }], created_at: "2026-10-05T00:00:00Z" },
      { id: "assistant-first", session_id: "session-first", role: "assistant", parts: [{ type: "text", text: "已完成分析" }], created_at: "2026-10-05T00:00:01Z" },
    ] : []);
    if (path.endsWith("/c/session-first")) return json(session());
    return json([]);
  }));
  render(<App />); const actor = userEvent.setup();
  await actor.type(await screen.findByLabelText("消息内容"), "帮我分析蛋白质结构");
  await actor.click(screen.getByRole("button", { name: "发送消息" }));
  await waitFor(() => expect(created).toMatchObject({ auto_title: true }));
  expect(await screen.findByText("已完成分析", {}, { timeout: 5000 })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "停止生成" })).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "帮我分析蛋白质结构" })).toBeInTheDocument();
  await actor.type(screen.getByLabelText("消息内容"), "下一轮草稿");
  named = true;
  if (networkError) {
    const readsBeforeError = listReads;
    await waitFor(() => expect(listReads).toBeGreaterThan(readsBeforeError), { timeout: 3500 });
    expect(screen.getByRole("link", { name: "帮我分析蛋白质结构" })).toBeInTheDocument();
  } else {
    expect(await screen.findByRole("link", { name: "蛋白质结构分析" }, { timeout: 3500 })).toBeInTheDocument();
  }
  expect(screen.getByLabelText("消息内容")).toHaveValue("下一轮草稿");
  expect(window.location.pathname).toBe(projectId ? `/p/${projectId}/c/session-first` : "/session/session-first");
  const readsWhenNamed = listReads;
  await new Promise((resolve) => setTimeout(resolve, 1200));
  expect(listReads).toBe(readsWhenNamed);
}, 10_000);
