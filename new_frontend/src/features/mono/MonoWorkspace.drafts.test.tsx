import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "../../app/App";
import { useComposerStore } from "../chat/composerStore";

afterEach(() => {
  cleanup();
  useComposerStore.getState().clear();
  window.localStorage.clear();
  window.history.replaceState(null, "", "/");
  vi.unstubAllGlobals();
});

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
function setup(path: string, submit?: () => Promise<Response>) {
  window.localStorage.setItem("research_access_token", "draft-test-only");
  window.history.replaceState(null, "", path);
  const sessions = [
    { id: "a", project_id: "project-alice", title: "会话 A", status: "idle" },
    { id: "b", project_id: "project-alice", title: "会话 B", status: "idle" },
  ];
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/me")) return json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (url.endsWith("/g")) return json([{ id: "project-alice", name: "Personal", description: "" }]);
    if (url.endsWith("/c") && init?.method === "POST") {
      const session = { id: "created", project_id: "project-alice", title: "新建会话", status: "idle" };
      sessions.push(session);
      return json(session);
    }
    if (url.endsWith("/messages") && init?.method === "POST") return submit ? submit() : json({ run_id: "run-new" });
    if (url.endsWith("/c") && (!init?.method || init.method === "GET")) return json(sessions);
    if (url.endsWith("/models")) return json([
      { id: "first-model", supports_images: false, reasoning_levels: ["medium", "high"] },
      { id: "second-model", supports_images: true, reasoning_levels: ["medium", "high"] },
    ]);
    if (["/messages", "/skills", "/resources", "/files", "/artifacts"].some((suffix) => url.endsWith(suffix))) return json([]);
    return json({ detail: "Not found" }, 404);
  });
  vi.stubGlobal("fetch", fetcher);
  return { fetcher, sessions };
}

it("isolates routed chat drafts and restores them from browser storage after reopening", async () => {
  const { fetcher } = setup("/session/a");
  const actor = userEvent.setup();
  render(<App />);
  await actor.type(await screen.findByLabelText("消息内容"), "A 的草稿");
  await actor.click(screen.getByRole("link", { name: "会话 B" }));
  expect(await screen.findByLabelText("消息内容")).toHaveValue("");
  await actor.type(screen.getByLabelText("消息内容"), "B 的草稿");
  await actor.click(screen.getByRole("link", { name: "会话 A" }));
  expect(await screen.findByLabelText("消息内容")).toHaveValue("A 的草稿");
  await actor.click(screen.getByRole("link", { name: "新聊天" }));
  expect(await screen.findByLabelText("消息内容")).toHaveValue("");
  cleanup();
  window.history.replaceState(null, "", "/session/b");
  render(<App />);
  await waitFor(() => expect(screen.getByLabelText("消息内容")).toHaveValue("B 的草稿"));
  expect(fetcher.mock.calls.filter(([, init]) => init?.method && init.method !== "GET")).toHaveLength(0);
}, 15_000);

it("moves a new-chat draft to its created session and retains it when submission is rejected", async () => {
  let finish!: (response: Response) => void;
  setup("/", () => new Promise((resolve) => { finish = resolve; }));
  const actor = userEvent.setup();
  render(<App />);
  await actor.type(await screen.findByLabelText("消息内容"), "首次消息尚未提交");
  await actor.click(screen.getByRole("button", { name: "发送消息" }));
  await waitFor(() => expect(window.location.pathname).toBe("/session/created"));
  await waitFor(() => expect(screen.getByLabelText("消息内容")).toHaveValue("首次消息尚未提交"));
  finish(json({ detail: { code: "TOKEN_QUOTA_EXCEEDED" } }, 409));
  expect(await screen.findByRole("alert")).toHaveTextContent("Token");
  expect(screen.getByLabelText("消息内容")).toHaveValue("首次消息尚未提交");
  await actor.click(screen.getByRole("link", { name: "新聊天" }));
  expect(screen.getByLabelText("消息内容")).toHaveValue("");
  cleanup();
  window.history.replaceState(null, "", "/session/created");
  render(<App />);
  expect(await screen.findByLabelText("消息内容")).toHaveValue("首次消息尚未提交");
});

it("clears a successfully created chat draft locally while preserving its model preference", async () => {
  let finish!: (response: Response) => void;
  setup("/", () => new Promise((resolve) => { finish = resolve; }));
  const actor = userEvent.setup();
  render(<App />);
  await actor.type(await screen.findByLabelText("消息内容"), "首次提交成功");
  await actor.click(screen.getByRole("button", { name: "发送消息" }));
  await waitFor(() => expect(window.location.pathname).toBe("/session/created"));
  await waitFor(() => expect(screen.getByLabelText("消息内容")).toHaveValue("首次提交成功"));
  finish(json({ run_id: "run-new" }));
  await waitFor(() => expect(screen.getByLabelText("消息内容")).toHaveValue(""));
  cleanup();
  window.history.replaceState(null, "", "/session/created");
  render(<App />);
  expect(await screen.findByLabelText("消息内容")).toHaveValue("");
  await waitFor(() => expect(screen.getByRole("button", { name: /选择模型与推理强度/ })).toHaveTextContent("first-model默认"));
});
