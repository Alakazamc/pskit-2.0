import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "../../app/App";
import { useComposerStore } from "../chat/composerStore";

afterEach(() => {
  cleanup();
  useComposerStore.getState().clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.localStorage.clear();
  window.history.replaceState(null, "", "/");
});

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
function loggedIn(path: string) {
  window.localStorage.setItem("research_access_token", "demo-token");
  window.history.replaceState(null, "", path);
}

function runEvents(...events: unknown[]) {
  return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(""), {
    headers: { "content-type": "text/event-stream" },
  });
}

it("keeps a guest draft and offers upgrade when its Token allowance is exhausted", async () => {
  loggedIn("/session/session-1");
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ id: "guest-1", name: "Guest", email: "", is_anonymous: true });
    if (path.endsWith("/g")) return json([{ id: "project-guest-1", name: "Personal", description: "" }]);
    if (path.endsWith("/c")) return json([{ id: "session-1", project_id: "project-guest-1", title: "Research" }]);
    if (path.endsWith("/c/session-1/messages") && init?.method === "POST")
      return json({ detail: { code: "TOKEN_QUOTA_EXCEEDED" } }, 409);
    if (path.endsWith("/messages") || path.endsWith("/skills") || path.endsWith("/resources")) return json([]);
    return json({ detail: "Not found" }, 404);
  });
  vi.stubGlobal("fetch", fetcher);
  render(<App />);
  const actor = userEvent.setup();

  await actor.type(await screen.findByLabelText("消息内容"), "Analyze this dataset");
  await actor.click(screen.getByRole("button", { name: "发送消息" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("本月 Token 额度已用尽。");
  expect(screen.getByLabelText("消息内容")).toHaveValue("Analyze this dataset");
  expect(screen.getByRole("link", { name: "升级账号以继续" })).toHaveAttribute("href", "/settings");
});

it("uploads a file through Python even with demo authentication and sends its server ID", async () => {
  loggedIn("/c/session-1");
  const sent: unknown[] = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (path.endsWith("/g")) return json([{ id: "project-alice", name: "Personal", description: "" }]);
    if (path.endsWith("/c")) return json([
      { id: "session-1", project_id: "project-alice", title: "Review notes", status: "idle" },
    ]);
    if (path.includes("/files/content?name=notes.txt") && init?.method === "PUT")
      return json({ id: "file-from-python", name: "notes.txt", size: 5, status: "ready" });
    if (path.endsWith("/c/session-1/messages") && init?.method === "POST") {
      sent.push(JSON.parse(String(init.body)));
      return json({ run_id: "run-1" });
    }
    if (path.includes("/runs/run-1/events")) return runEvents();
    if (path.endsWith("/messages") || path.endsWith("/skills") || path.endsWith("/resources")
      || path.endsWith("/files")) return json([]);
    return json({ detail: "Not found" }, 404);
  });
  vi.stubGlobal("fetch", fetcher);
  const { container } = render(<App />);
  const actor = userEvent.setup();

  const input = await waitFor(() => {
    const found = container.querySelector<HTMLInputElement>('input[type="file"]');
    expect(found).not.toBeNull();
    return found!;
  }, { timeout: 5_000 });
  await actor.upload(input, new File(["notes"], "notes.txt", { type: "text/plain" }));
  expect(await screen.findByRole("group", { name: "已上传 notes.txt" }, { timeout: 5_000 })).toBeInTheDocument();
  expect(fetcher.mock.calls.some(([path, init]) =>
    String(path).includes("/files/content?name=notes.txt") && init?.method === "PUT",
  )).toBe(true);
  await actor.type(screen.getByLabelText("消息内容"), "Review this file");
  await actor.click(screen.getByRole("button", { name: "发送消息" }));
  await waitFor(() => expect(sent).toEqual([expect.objectContaining({
    attachments: [{ id: "file-from-python", name: "notes.txt" }],
  })]));
});

it.each([
  ["批准执行", "approved"],
  ["拒绝", "rejected"],
] as const)("submits an AF3 approval decision from the active chat: %s", async (button, decision) => {
  loggedIn("/c/session-1");
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (path.endsWith("/g")) return json([{ id: "project-alice", name: "Personal", description: "" }]);
    if (path.endsWith("/c")) return json([
      { id: "session-1", project_id: "project-alice", title: "AF3 task", latest_run_id: "run-1" },
    ]);
    if (path.includes("/runs/run-1/events")) return runEvents({
      id: "1", run_id: "run-1", type: "approval.required",
      data: { approval_id: "approval-1", capability: "submit_af3", estimated_gpu_minutes: 42 },
    });
    if (path.endsWith("/runs/run-1/approvals/approval-1") && init?.method === "POST") return json({
      approval_id: "approval-1", status: decision, job_id: decision === "approved" ? "job-1" : null,
    });
    if (path.endsWith("/messages") || path.endsWith("/skills") || path.endsWith("/resources")) return json([]);
    return json({ detail: "Not found" }, 404);
  });
  vi.stubGlobal("fetch", fetcher);
  render(<App />);

  await waitFor(() => expect(fetcher.mock.calls.some(([input]) =>
    String(input).includes("/runs/run-1/events"),
  )).toBe(true), { timeout: 5_000 });
  const controls = await screen.findByRole("region", { name: "当前运行" }, { timeout: 5_000 });
  expect(within(controls).getByText(/42 GPU 分钟/)).toBeInTheDocument();
  await userEvent.setup().click(within(controls).getByRole("button", { name: button }));
  await waitFor(() => expect(fetcher.mock.calls.some(([input, init]) =>
    String(input).endsWith("/runs/run-1/approvals/approval-1")
      && init?.method === "POST"
      && JSON.parse(String(init.body)).decision === decision,
  )).toBe(true));
  expect(fetcher.mock.calls.some(([input, init]) =>
    String(input).endsWith("/messages") && init?.method === "POST",
  )).toBe(false);
});

it("shows waiting inside the reply and cancels without a bar above the composer", async () => {
  loggedIn("/c/session-1");
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (path.endsWith("/g")) return json([{ id: "project-alice", name: "Personal", description: "" }]);
    if (path.endsWith("/c")) return json([
      { id: "session-1", project_id: "project-alice", title: "AF3 task", latest_run_id: "run-1" },
    ]);
    if (path.includes("/runs/run-1/events")) return runEvents({
      id: "1", run_id: "run-1", type: "task.updated",
      data: { job_id: "job-1", status: "running", progress: 25 },
    });
    if (path.endsWith("/runs/run-1") && init?.method === "DELETE")
      return json({ run_id: "run-1", status: "cancelled" });
    if (path.endsWith("/messages") || path.endsWith("/skills") || path.endsWith("/resources")) return json([]);
    return json({ detail: "Not found" }, 404);
  });
  vi.stubGlobal("fetch", fetcher);
  render(<App />);

  const spinner = await screen.findByRole("status", { name: "正在生成回复" });
  expect(spinner.closest(".message-row.assistant")).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "当前运行" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "发送消息" })).not.toBeInTheDocument();
  await userEvent.setup().click(screen.getByRole("button", { name: "取消运行" }));
  await waitFor(() => expect(fetcher.mock.calls.some(([input, init]) =>
    String(input).endsWith("/runs/run-1") && init?.method === "DELETE",
  )).toBe(true));
});

it("shows stop for a reloaded active run before the first event arrives", async () => {
  loggedIn("/session/session-1");
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (path.endsWith("/g")) return json([]);
    if (path.endsWith("/c")) return json([{ id: "session-1", project_id: "project-alice", title: "Queued", status: "running", latest_run_id: "run-queued" }]);
    if (path.includes("/runs/run-queued/events")) return runEvents();
    return json([]);
  }));
  render(<App />);
  expect(await screen.findByRole("button", { name: "取消运行" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: "发送消息" })).not.toBeInTheDocument();
});

it("switches the composer to stop after message submission while the reply is streaming", async () => {
  loggedIn("/session/session-1");
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (path.endsWith("/g")) return json([{ id: "project-alice", name: "Personal", description: "" }]);
    if (path.endsWith("/c")) return json([{ id: "session-1", project_id: "project-alice", title: "Research" }]);
    if (path.endsWith("/c/session-1/messages") && init?.method === "POST") return json({ run_id: "run-live" });
    if (path.includes("/runs/run-live/events")) return runEvents({
      id: "1", run_id: "run-live", type: "message.delta", data: { delta: "正在分析" },
    });
    if (path.endsWith("/runs/run-live") && init?.method === "DELETE") return json({ run_id: "run-live", status: "cancelled" });
    if (path.endsWith("/messages") || path.endsWith("/skills") || path.endsWith("/resources")) return json([]);
    return json({ detail: "Not found" }, 404);
  });
  vi.stubGlobal("fetch", fetcher);
  render(<App />);
  const actor = userEvent.setup();
  await actor.type(await screen.findByLabelText("消息内容"), "分析数据");
  await actor.click(screen.getByRole("button", { name: "发送消息" }));
  expect(await screen.findByText("正在分析")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "取消运行" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: "发送消息" })).not.toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "当前运行" })).not.toBeInTheDocument();
});

it("shows a quota rejection on approval and keeps the decision available", async () => {
  loggedIn("/c/session-1");
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (path.endsWith("/g")) return json([{ id: "project-alice", name: "Personal", description: "" }]);
    if (path.endsWith("/c")) return json([
      { id: "session-1", project_id: "project-alice", title: "AF3 task", latest_run_id: "run-1" },
    ]);
    if (path.includes("/runs/run-1/events")) return runEvents({
      id: "1", run_id: "run-1", type: "approval.required",
      data: { approval_id: "approval-1", capability: "submit_af3", estimated_gpu_minutes: 42 },
    });
    if (path.endsWith("/runs/run-1/approvals/approval-1"))
      return json({ detail: { code: "GPU_DAILY_QUOTA_EXCEEDED" } }, 409);
    if (path.endsWith("/messages") || path.endsWith("/skills") || path.endsWith("/resources")) return json([]);
    return json({ detail: "Not found" }, 404);
  }));
  render(<App />);
  const controls = await screen.findByRole("region", { name: "当前运行" });
  await userEvent.setup().click(within(controls).getByRole("button", { name: "批准执行" }));
  expect(await within(controls).findByRole("alert")).toHaveTextContent("今日 GPU 额度不足");
  expect(within(controls).getByRole("button", { name: "批准执行" })).toBeEnabled();
});

it("shows server Token and daily GPU limits with pending GPU reconciliation in settings", async () => {
  loggedIn("/settings");
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (path.endsWith("/usage/entries")) return json([{
      id: "job-1", resource: "gpu_minutes", kind: "job", amount: 20,
      status: "pending_reconciliation", period: "2026-10-01", run_id: "run-1",
      job_id: "af3-1", created_at: "2026-10-01T08:00:00Z",
    }]);
    if (path.endsWith("/usage")) return json({
      tokens: { limit: 1000, used: 125, reserved: 0, remaining: 875, unit: "tokens", period: "month", resets_at: "2026-11-01T00:00:00Z" },
      gpu: { limit: 60, used: 12, reserved: 20, remaining: 28, unit: "gpu_minutes", period: "day", resets_at: "2026-10-02T00:00:00Z" },
    });
    if (path.endsWith("/g") || path.endsWith("/c") || path.endsWith("/skills") || path.endsWith("/resources")) return json([]);
    return json({ detail: "Not found" }, 404);
  });
  vi.stubGlobal("fetch", fetcher);
  render(<App />);

  const usage = await screen.findByRole("region", { name: "用量与配额" });
  expect(document.querySelector(".mono-topbar-left > h1")).toHaveTextContent("设置");
  expect(within(screen.getByRole("main")).getAllByRole("heading", { level: 1 })).toHaveLength(1);
  expect(await within(usage).findByText("Token 月额度")).toBeInTheDocument();
  expect(within(usage).getByText("875")).toBeInTheDocument();
  expect(within(usage).getAllByText("GPU 每日额度").length).toBeGreaterThan(0);
  expect(within(usage).getByText("28")).toBeInTheDocument();
  expect(within(usage).getByText(/预留 20/)).toBeInTheDocument();
  expect(await within(usage).findByText("待对账")).toBeInTheDocument();
  expect(fetcher.mock.calls.some(([input]) => String(input).endsWith("/usage/entries"))).toBe(true);
  expect(within(usage).queryByText(/New API/)).not.toBeInTheDocument();
});

it("localizes the active Skill navigation and page title", async () => {
  loggedIn("/skills");
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (path.endsWith("/g") || path.endsWith("/c")
      || path.endsWith("/skills") || path.endsWith("/resources")) return json([]);
    return json({ detail: "Not found" }, 404);
  }));
  render(<App />);
  expect(await screen.findByRole("heading", { name: "技能", level: 1 })).toBeInTheDocument();
  expect(document.querySelector(".mono-topbar-left > h1")).toHaveTextContent("技能");
  expect(within(screen.getByRole("main")).getAllByRole("heading", { level: 1 })).toHaveLength(1);
  expect(within(screen.getByRole("navigation", { name: "主导航" })).getByRole("link", { name: "技能" })).toBeInTheDocument();
});

it("labels the same quota and pending usage in English", async () => {
  loggedIn("/settings");
  window.localStorage.setItem("research_language", "en");
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (path.endsWith("/usage/entries")) return json([{
      id: "job-1", resource: "gpu_minutes", kind: "job", amount: 20,
      status: "pending_reconciliation", period: "2026-10-01", run_id: null,
      job_id: "af3-1", created_at: "2026-10-01T08:00:00Z",
    }]);
    if (path.endsWith("/usage")) return json({
      tokens: { limit: 1000, used: 125, reserved: 0, remaining: 875, unit: "tokens", period: "month", resets_at: "2026-11-01T00:00:00Z" },
      gpu: { limit: 60, used: 12, reserved: 20, remaining: 28, unit: "gpu_minutes", period: "day", resets_at: "2026-10-02T00:00:00Z" },
    });
    if (path.endsWith("/g") || path.endsWith("/c") || path.endsWith("/skills") || path.endsWith("/resources")) return json([]);
    return json({ detail: "Not found" }, 404);
  }));
  render(<App />);
  const usage = await screen.findByRole("region", { name: "Usage and limits" });
  expect(await within(usage).findByText("Monthly Token quota")).toBeInTheDocument();
  expect(within(usage).getAllByText("Daily GPU quota").length).toBeGreaterThan(0);
  expect(await within(usage).findByText("Pending reconciliation")).toBeInTheDocument();
});

it("keeps the composer menu inside the themed workspace", async () => {
  loggedIn("/");
  window.localStorage.setItem("pskit-theme", "dark");
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (path.endsWith("/g") || path.endsWith("/c") || path.endsWith("/skills") || path.endsWith("/resources")) return json([]);
    return json({ detail: "Not found" }, 404);
  }));
  render(<App />);

  await userEvent.setup().click(await screen.findByRole("button", { name: "添加上下文" }));
  const menu = screen.getByRole("menu");
  expect(menu.closest(".mono-app.dark")).not.toBeNull();
  expect(within(menu).getByRole("menuitem", { name: "添加文件引用" })).toBeInTheDocument();
});

it("opens chat search from one sidebar button and finds chats across projects", async () => {
  loggedIn("/");
  const actor = userEvent.setup();
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (path.endsWith("/g")) return json([{ id: "project-alice", name: "Personal", description: "" }, { id: "project-lab", name: "Lab research", description: "" }]);
    if (path.endsWith("/g/g-p-lab/c")) return json([{ id: "chat-lab", project_id: "project-lab", title: "蛋白结构预测" }]);
    if (path.endsWith("/c")) return json([{ id: "chat-personal", project_id: "project-alice", title: "P53 notebook" }]);
    if (path.endsWith("/skills") || path.endsWith("/resources") || path.endsWith("/messages")) return json([]);
    return json({ detail: "Not found" }, 404);
  }));
  render(<App />);

  const searchButton = await screen.findByRole("button", { name: "搜索聊天" });
  expect(screen.queryByRole("searchbox")).not.toBeInTheDocument();
  await actor.click(searchButton);
  const dialog = screen.getByRole("dialog", { name: "搜索聊天" });
  expect(within(dialog).getByText("最近聊天")).toBeInTheDocument();
  expect(await within(dialog).findByRole("link", { name: /蛋白结构预测/ })).toBeInTheDocument();
  const input = within(dialog).getByRole("searchbox", { name: "搜索聊天" });
  await actor.type(input, "蛋白");
  expect(within(dialog).queryByRole("link", { name: /P53 notebook/ })).not.toBeInTheDocument();
  await actor.click(within(dialog).getByRole("link", { name: /蛋白结构预测/ }));
  await waitFor(() => expect(window.location.pathname).toBe("/p/project-lab/c/chat-lab"));
  expect(screen.queryByRole("dialog", { name: "搜索聊天" })).not.toBeInTheDocument();

  await actor.click(screen.getByRole("button", { name: "收起侧栏" }));
  await actor.click(screen.getByRole("button", { name: "搜索聊天" }));
  expect(screen.getByRole("dialog", { name: "搜索聊天" })).toBeInTheDocument();
  await actor.keyboard("{Escape}");
  expect(screen.queryByRole("dialog", { name: "搜索聊天" })).not.toBeInTheDocument();
});

it("renames a personal chat from its sidebar menu and refreshes the title", async () => {
  loggedIn("/c/session-1");
  const actor = userEvent.setup();
  let title = "旧标题";
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (path.endsWith("/g")) return json([{ id: "project-alice", name: "Personal", description: "" }]);
    if (path.endsWith("/c")) return json([{ id: "session-1", project_id: "project-alice", title }]);
    if (path.endsWith("/c/session-1") && init?.method === "PATCH") {
      title = JSON.parse(String(init.body)).title;
      return json({ id: "session-1", project_id: "project-alice", title });
    }
    if (path.endsWith("/messages") || path.endsWith("/skills") || path.endsWith("/resources")) return json([]);
    return json({ detail: "Not found" }, 404);
  });
  vi.stubGlobal("fetch", fetcher);
  render(<App />);

  expect(await screen.findByRole("link", { name: "旧标题" })).toBeInTheDocument();
  await actor.click(screen.getByRole("button", { name: "更多操作：旧标题" }));
  await actor.click(screen.getByRole("menuitem", { name: "重命名" }));
  const dialog = screen.getByRole("dialog", { name: "重命名对话" });
  const input = within(dialog).getByRole("textbox", { name: "对话名称" });
  expect(input).toHaveFocus();
  await actor.clear(input);
  await actor.type(input, "新标题");
  await actor.click(within(dialog).getByRole("button", { name: "保存" }));

  await waitFor(() => expect(fetcher.mock.calls.some(([input, init]) =>
    String(input).endsWith("/c/session-1") && init?.method === "PATCH"
      && JSON.parse(String(init.body)).title === "新标题",
  )).toBe(true));
  expect(await screen.findByRole("link", { name: "新标题" })).toHaveAttribute("aria-current", "page");
});

it("creates a project directly from the sidebar in mock mode", async () => {
  loggedIn("/");
  const actor = userEvent.setup();
  const projects = [{ id: "project-alice", name: "Personal", description: "" }];
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (path.endsWith("/g") && init?.method === "POST") {
      const project = { id: "project-new", ...JSON.parse(String(init.body)) };
      projects.push(project);
      return json(project, 201);
    }
    if (path.endsWith("/g")) return json(projects);
    if (path.endsWith("/c") || path.endsWith("/skills") || path.endsWith("/resources") || path.endsWith("/tool-runs")) return json([]);
    if (path.endsWith("/g/g-p-new/skills")) return json({ skill_ids: [], default_skill_ids: [] });
    return json({ detail: "Not found" }, 404);
  });
  vi.stubGlobal("fetch", fetcher);
  render(<App />);

  await actor.click(await screen.findByRole("button", { name: "添加项目" }));
  const dialog = screen.getByRole("dialog", { name: "新建项目" });
  const nameInput = within(dialog).getByRole("textbox", { name: "项目名称" });
  expect(nameInput).toHaveFocus();
  await actor.type(nameInput, "蛋白设计");
  await actor.click(within(dialog).getByRole("button", { name: "选择项目图标" }));
  await actor.type(screen.getByRole("searchbox", { name: "搜索图标" }), "DNA");
  expect(screen.queryByRole("button", { name: "文件夹" })).not.toBeInTheDocument();
  await actor.click(screen.getByRole("button", { name: "DNA" }));
  await actor.click(within(dialog).getByRole("button", { name: "创建项目" }));

  await waitFor(() => expect(fetcher.mock.calls.some(([input, init]) =>
    String(input).endsWith("/g") && init?.method === "POST"
      && JSON.parse(String(init.body)).name === "蛋白设计"
      && JSON.parse(String(init.body)).icon === "dna",
  )).toBe(true));
  expect(window.location.pathname).toBe("/p/project-new");
  await waitFor(() => expect(screen.getByRole("link", { name: "蛋白设计" })).toHaveAttribute("aria-current", "page"));
  expect(screen.getByRole("link", { name: "蛋白设计" }).querySelector('[data-project-icon="dna"]')).toBeInTheDocument();
});

it("uses the project header as the only page title and opens creation there", async () => {
  loggedIn("/g");
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (path.endsWith("/g")) return json([{ id: "project-alice", name: "个人", description: "" }]);
    if (path.endsWith("/c") || path.endsWith("/skills") || path.endsWith("/resources")) return json([]);
    return json({ detail: "Not found" }, 404);
  }));
  render(<App />);
  const title = await screen.findByRole("heading", { name: "项目", level: 1 });
  expect(title.closest(".mono-topbar-left")).not.toBeNull();
  expect(within(screen.getByRole("main")).getAllByRole("heading", { level: 1 })).toHaveLength(1);
  await userEvent.setup().click(screen.getByRole("button", { name: "新建项目" }));
  expect(screen.getByRole("dialog", { name: "新建项目" })).toBeInTheDocument();
});

it("changes a project icon from its detail page and updates the sidebar", async () => {
  loggedIn("/p/project-lab");
  const actor = userEvent.setup();
  let icon = "flask";
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (path.endsWith("/g/g-p-lab/icon") && init?.method === "PATCH") {
      icon = JSON.parse(String(init.body)).icon;
      return json({ id: "project-lab", name: "蛋白研究", description: "", icon });
    }
    if (path.endsWith("/g")) return json([
      { id: "project-alice", name: "个人", description: "", icon: "folder" },
      { id: "project-lab", name: "蛋白研究", description: "", icon },
    ]);
    if (path.endsWith("/g/g-p-lab/skills")) return json({ skill_ids: [], default_skill_ids: [] });
    if (path.endsWith("/c") || path.endsWith("/skills") || path.endsWith("/resources") || path.endsWith("/tool-runs")) return json([]);
    return json({ detail: "Not found" }, 404);
  });
  vi.stubGlobal("fetch", fetcher);
  render(<App />);
  await actor.click(await screen.findByRole("button", { name: "更换项目图标" }));
  const dialog = screen.getByRole("dialog", { name: "项目图标" });
  await actor.click(within(dialog).getByRole("button", { name: "显微镜" }));
  await actor.click(within(dialog).getByRole("button", { name: "保存图标" }));
  await waitFor(() => expect(fetcher.mock.calls.some(([input, init]) =>
    String(input).endsWith("/g/g-p-lab/icon") && init?.method === "PATCH"
      && JSON.parse(String(init.body)).icon === "microscope",
  )).toBe(true));
  await waitFor(() => expect(screen.getByRole("link", { name: "蛋白研究" }).querySelector('[data-project-icon="microscope"]')).toBeInTheDocument());
});

it("creates a project, saves its default Skill, and moves a personal chat into it", async () => {
  loggedIn("/g");
  const actor = userEvent.setup();
  const projects: { id: string; name: string; description: string }[] = [{ id: "project-alice", name: "我的科研项目", description: "" }];
  let skillSettings = { skill_ids: [] as string[], default_skill_ids: [] as string[] };
  let sessionProject = "project-alice";
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    if (path.endsWith("/me")) return json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (path.endsWith("/g") && method === "GET") return json(projects);
    if (path.endsWith("/g") && method === "POST") {
      const body = JSON.parse(String(init?.body));
      const project = { id: "project-new", name: body.name, description: body.description };
      projects.push(project);
      return json(project, 201);
    }
    if (path.endsWith("/g/g-p-new/skills") && method === "GET") return json(skillSettings);
    if (path.endsWith("/tool-runs") && method === "GET") return json([]);
    if (path.endsWith("/g/g-p-new/skills") && method === "PUT") {
      skillSettings = JSON.parse(String(init?.body));
      return json(skillSettings);
    }
    if (path.endsWith("/c/session-1/project") && method === "PATCH") {
      sessionProject = JSON.parse(String(init?.body)).project_id;
      return json({ id: "session-1", project_id: sessionProject, title: "P53 结构", status: "idle" });
    }
    if (path.endsWith("/c") && method === "GET") {
      const projectId = path.endsWith("/g/g-p-new/c") ? "project-new" : "project-alice";
      return json(projectId === sessionProject ? [{ id: "session-1", project_id: sessionProject, title: "P53 结构", status: "idle" }] : []);
    }
    if (path.endsWith("/skills")) return json([{ id: "structure-review", name: "Structure review", description: "检查结构" }]);
    if (path.endsWith("/resources") || path.endsWith("/messages")) return json([]);
    return json({ detail: "Not found" }, 404);
  });
  vi.stubGlobal("fetch", fetcher);
  render(<App />);

  await actor.click(await screen.findByRole("button", { name: "新建项目" }));
  const createDialog = screen.getByRole("dialog", { name: "新建项目" });
  await actor.type(within(createDialog).getByRole("textbox", { name: "项目名称" }), "蛋白设计");
  await actor.click(within(createDialog).getByRole("button", { name: "创建项目" }));
  expect(await screen.findByRole("heading", { name: "蛋白设计", level: 1 })).toBeInTheDocument();
  await actor.click(screen.getByRole("button", { name: "管理" }));
  const skillDialog = screen.getByRole("dialog", { name: "项目 Skill" });
  await actor.click(within(skillDialog).getByRole("checkbox", { name: "加入项目" }));
  await actor.click(within(skillDialog).getByRole("checkbox", { name: "默认启用" }));
  await actor.click(within(skillDialog).getByRole("button", { name: "保存设置" }));
  await waitFor(() => expect(skillSettings.default_skill_ids).toEqual(["structure-review"]));
  expect(await screen.findByText("✦ Structure review")).toBeInTheDocument();

  await actor.click(screen.getByRole("link", { name: "新聊天" }));
  await actor.click(await screen.findByRole("link", { name: "P53 结构" }));
  await actor.click(screen.getByRole("button", { name: "移入项目" }));
  const moveDialog = screen.getByRole("dialog", { name: "移入项目" });
  await actor.click(within(moveDialog).getByRole("button", { name: "蛋白设计" }));
  await waitFor(() => expect(sessionProject).toBe("project-new"));
  expect(window.location.pathname).toBe("/p/project-new/c/session-1");
  await actor.type(await screen.findByLabelText("消息内容"), "/");
  expect(await screen.findByText("项目 Skill")).toBeInTheDocument();
}, 15_000);

it("runs a PDB search and shows the returned record with a source link", async () => {
  loggedIn("/tools/pdb");
  const actor = userEvent.setup();
  const invoked: unknown[] = [];
  let invocationKey: string | null = null;
  let toolRun: { id: string; tool: string; title: string; arguments: { query: string }; result: { hits: { pdb_id: string; title: string; score: number }[] }; created_at: string; project_id: string | null } | null = null;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (path.endsWith("/g")) return json([{ id: "project-alice", name: "我的科研项目", description: "" }, { id: "project-new", name: "蛋白设计", description: "" }]);
    if (path.endsWith("/c") || path.endsWith("/skills") || path.endsWith("/resources")) return json([]);
    if (path.endsWith("/tool-runs")) return json(toolRun ? [toolRun] : []);
    if (path.endsWith("/tool-runs/toolrun-1/project")) {
      toolRun = { ...toolRun!, project_id: JSON.parse(String(init?.body)).project_id };
      return json(toolRun);
    }
    if (path.endsWith("/mcp/tools")) return json([{ name: "search_pdb", description: "Search" }]);
    if (path.endsWith("/mcp/tools/search_pdb/invoke")) {
      invoked.push(JSON.parse(String(init?.body)));
      invocationKey = new Headers(init?.headers).get("Idempotency-Key");
      toolRun = { id: "toolrun-1", tool: "search_pdb", title: "search_pdb: p53", arguments: { query: "p53" }, result: { hits: [{ pdb_id: "1A9N", title: "Protein-RNA complex", score: 0.94 }] }, created_at: "2026-10-01T00:00:00Z", project_id: null };
      return json({ tool: "search_pdb", status: "completed", run_id: "toolrun-1", result: toolRun.result });
    }
    return json({ detail: "Not found" }, 404);
  }));
  render(<App />);
  await actor.type(await screen.findByRole("textbox", { name: "蛋白名称、UniProt 或 PDB ID" }), "p53");
  await actor.click(screen.getByRole("button", { name: "检索结构" }));
  expect(await screen.findByText("1A9N")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /查看 PDB 原始记录/ })).toHaveAttribute("href", "https://www.rcsb.org/structure/1A9N");
  expect(screen.getByRole("link", { name: /查看三维结构/ })).toHaveAttribute("href", "/tools/structure?pdb=1A9N");
  expect(invoked).toEqual([{ query: "p53" }]);
  expect(invocationKey ?? "").toMatch(/^[0-9a-f-]{36}$/);
  await actor.click(within(screen.getByRole("navigation", { name: "主导航" })).getByRole("link", { name: "工具集" }));
  await actor.click(await screen.findByRole("link", { name: "我的运行" }));
  expect(await screen.findByText("search_pdb: p53")).toBeInTheDocument();
  await actor.selectOptions(screen.getByRole("combobox", { name: "保存 search_pdb: p53 到项目" }), "project-new");
  await waitFor(() => expect(toolRun?.project_id).toBe("project-new"));
  expect(await screen.findByText("已保存 · 蛋白设计")).toBeInTheDocument();
}, 10_000);

it("reuses a PDB invocation key when the same submission is retried after an uncertain failure", async () => {
  loggedIn("/tools/pdb");
  const actor = userEvent.setup();
  const keys: (string | null)[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (path.endsWith("/g") || path.endsWith("/c") || path.endsWith("/skills")
      || path.endsWith("/resources") || path.endsWith("/tool-runs")) return json([]);
    if (path.endsWith("/mcp/tools")) return json([{ name: "search_pdb", description: "Search" }]);
    if (path.endsWith("/mcp/tools/search_pdb/invoke")) {
      keys.push(new Headers(init?.headers).get("Idempotency-Key"));
      if (keys.length === 1) throw new TypeError("response lost after execution");
      return json({ tool: "search_pdb", status: "completed", run_id: "toolrun-1", result: { hits: [] } });
    }
    return json({ detail: "Not found" }, 404);
  }));
  render(<App />);
  await actor.type(await screen.findByRole("textbox", { name: "蛋白名称、UniProt 或 PDB ID" }), "p53");
  await actor.click(screen.getByRole("button", { name: "检索结构" }));
  await screen.findByRole("alert");
  await actor.click(screen.getByRole("button", { name: "检索结构" }));
  await waitFor(() => expect(keys).toHaveLength(2));
  expect(keys[0]).toBeTruthy();
  expect(keys[1]).toBe(keys[0]);
});

it("lists the structure viewer in the toolbox even without an MCP connection", async () => {
  loggedIn("/tools");
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (path.endsWith("/g")) return json([{ id: "project-alice", name: "Personal", description: "" }]);
    if (path.endsWith("/c") || path.endsWith("/skills") || path.endsWith("/resources") || path.endsWith("/mcp/tools")) return json([]);
    return json({ detail: "Not found" }, 404);
  }));
  render(<App />);
  const actor = userEvent.setup();
  const viewer = await screen.findByRole("link", { name: /结构查看器/ });
  expect(viewer).toHaveAttribute("href", "/tools/structure");
  await actor.click(viewer);
  expect(await screen.findByRole("heading", { name: "结构查看器", level: 1 })).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "PDB ID" })).toBeInTheDocument();
});

it("shows an uncertain MCP outcome without inviting a blind retry", async () => {
  loggedIn("/tools/pdb");
  const actor = userEvent.setup();
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (path.endsWith("/g") || path.endsWith("/c") || path.endsWith("/skills")
      || path.endsWith("/resources") || path.endsWith("/tool-runs")) return json([]);
    if (path.endsWith("/mcp/tools")) return json([{ name: "search_pdb", description: "Search" }]);
    if (path.endsWith("/mcp/tools/search_pdb/invoke"))
      return json({ detail: { code: "MCP_TOOL_CALL_OUTCOME_UNKNOWN" } }, 409);
    return json({ detail: "Not found" }, 404);
  }));
  render(<App />);
  await actor.type(await screen.findByRole("textbox", { name: "蛋白名称、UniProt 或 PDB ID" }), "p53");
  await actor.click(screen.getByRole("button", { name: "检索结构" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("本次工具调用的结果尚未确认");
});

it("shows the artifacts route in English when that language is selected", async () => {
  loggedIn("/artifacts");
  window.localStorage.setItem("research_language", "en");
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (path.endsWith("/g")) return json([]);
    if (path.endsWith("/c") || path.endsWith("/skills") || path.endsWith("/resources")) return json([]);
    if (path.endsWith("/artifacts")) return json([
      { id: "demo", name: "demo.cif", kind: "structure", available: false },
    ]);
    return json({ detail: "Not found" }, 404);
  }));
  render(<App />);
  expect(await screen.findByRole("heading", { name: "Artifacts", level: 1 })).toBeInTheDocument();
  expect(document.querySelector(".mono-topbar-left > h1")).toHaveTextContent("Artifacts");
  expect(within(screen.getByRole("main")).getAllByRole("heading", { level: 1 })).toHaveLength(1);
  expect(await screen.findByRole("button", { name: "Download demo.cif" })).toBeDisabled();
}, 10_000);

it("translates the active workspace navigation and settings page", async () => {
  loggedIn("/settings");
  window.localStorage.setItem("research_language", "en");
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (path.endsWith("/g") || path.endsWith("/c")
      || path.endsWith("/skills") || path.endsWith("/resources")) return json([]);
    return json({ detail: "Not found" }, 404);
  }));
  render(<App />);
  expect(await screen.findByRole("heading", { name: "Settings", level: 1 })).toBeInTheDocument();
  const nav = screen.getByRole("navigation", { name: "Main navigation" });
  expect(within(nav).getByRole("link", { name: "New chat" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Search chats" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Light" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Dark" })).toBeInTheDocument();
  await userEvent.setup().click(screen.getByRole("button", { name: "Switch to light mode" }));
  expect(document.querySelector(".mono-app")).toHaveAttribute("data-theme", "light");
  expect(window.localStorage.getItem("pskit-theme")).toBe("light");
}, 10_000);

it("translates project creation and the project Skill dialog", async () => {
  loggedIn("/g");
  window.localStorage.setItem("research_language", "en");
  const actor = userEvent.setup();
  const projects = [{ id: "project-lab", name: "Lab research", description: "" }];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (path.endsWith("/g")) return json(projects);
    if (path.endsWith("/g/g-p-lab/skills")) return json({ skill_ids: [], default_skill_ids: [] });
    if (path.endsWith("/tool-runs") || path.endsWith("/c") || path.endsWith("/resources")) return json([]);
    if (path.endsWith("/skills")) return json([{ id: "structure-review", name: "Structure review", description: "Review structure" }]);
    return json({ detail: "Not found" }, 404);
  }));
  render(<App />);
  expect(await screen.findByRole("heading", { name: "Projects", level: 1 })).toBeInTheDocument();
  await actor.click(screen.getByRole("button", { name: "New project" }));
  expect(screen.getByRole("dialog", { name: "New project" })).toBeInTheDocument();
  await actor.click(screen.getByRole("button", { name: "Cancel" }));
  await actor.click(within(screen.getByRole("main")).getByRole("link", { name: /Lab research/ }));
  await actor.click(await screen.findByRole("button", { name: "Manage" }));
  expect(screen.getByRole("dialog", { name: "Project Skills" })).toBeInTheDocument();
}, 10_000);

it("translates the PDB search workspace and tool run history", async () => {
  loggedIn("/tools/pdb");
  window.localStorage.setItem("research_language", "en");
  const actor = userEvent.setup();
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (path.endsWith("/g") || path.endsWith("/c") || path.endsWith("/skills")
      || path.endsWith("/resources") || path.endsWith("/tool-runs")) return json([]);
    if (path.endsWith("/mcp/tools")) return json([{ name: "search_pdb", description: "Search PDB" }]);
    return json({ detail: "Not found" }, 404);
  }));
  render(<App />);
  expect(await screen.findByRole("heading", { name: "PDB structure search", level: 1 })).toBeInTheDocument();
  expect(document.querySelector(".mono-topbar-left > h1")).toHaveTextContent("PDB structure search");
  expect(within(screen.getByRole("main")).getAllByRole("heading", { level: 1 })).toHaveLength(1);
  expect(screen.getByRole("textbox", { name: "Protein name, UniProt or PDB ID" })).toBeInTheDocument();
  await actor.click(within(screen.getByRole("navigation", { name: "Main navigation" })).getByRole("link", { name: "Tools" }));
  await actor.click(await screen.findByRole("link", { name: "My runs" }));
  expect(await screen.findByRole("heading", { name: "My runs", level: 1 })).toBeInTheDocument();
  expect(document.querySelector(".mono-topbar-left > h1")).toHaveTextContent("My runs");
  expect(within(screen.getByRole("main")).getAllByRole("heading", { level: 1 })).toHaveLength(1);
}, 10_000);

it("builds the tool directory from the MCP catalog instead of fixed prediction cards", async () => {
  loggedIn("/tools");
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (path.endsWith("/g") || path.endsWith("/c")
      || path.endsWith("/skills") || path.endsWith("/resources")) return json([]);
    if (path.endsWith("/mcp/tools")) return json([
      { name: "fetch_uniprot", description: "Fetch UniProt record", input_schema: {
        type: "object", properties: { accession: { type: "string" } }, required: ["accession"],
      } },
    ]);
    return json({ detail: "Not found" }, 404);
  }));
  render(<App />);
  expect(await screen.findByRole("link", { name: /fetch_uniprot/ })).toHaveAttribute(
    "href", "/tools/run/fetch_uniprot",
  );
  expect(document.querySelector(".mono-topbar-left > h1")).toHaveTextContent("工具集");
  expect(within(screen.getByRole("main")).getAllByRole("heading", { level: 1 })).toHaveLength(1);
  expect(screen.queryByRole("link", { name: /INABe/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: /AlphaFold 3/ })).not.toBeInTheDocument();
}, 10_000);

it("uses a published MCP tool schema to invoke a newly listed tool", async () => {
  loggedIn("/tools/run/fetch_uniprot");
  const actor = userEvent.setup();
  const invoked: Record<string, unknown>[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (path.endsWith("/g") || path.endsWith("/c")
      || path.endsWith("/skills") || path.endsWith("/resources")) return json([]);
    if (path.endsWith("/mcp/tools")) return json([
      { name: "fetch_uniprot", description: "Fetch UniProt record", input_schema: {
        type: "object", properties: { accession: { type: "string" } }, required: ["accession"],
      } },
    ]);
    if (path.endsWith("/mcp/tools/fetch_uniprot/invoke")) {
      invoked.push(JSON.parse(String(init?.body)));
      return json({ tool: "fetch_uniprot", status: "completed", result: { accession: "P12345" } });
    }
    return json({ detail: "Not found" }, 404);
  }));
  render(<App />);
  await actor.type(await screen.findByRole("textbox", { name: "accession" }), "P12345");
  expect(document.querySelector(".mono-topbar-left > h1")).toHaveTextContent("fetch_uniprot");
  expect(within(screen.getByRole("main")).getAllByRole("heading", { level: 1 })).toHaveLength(1);
  await actor.click(screen.getByRole("button", { name: "运行工具" }));
  expect(await screen.findByText(/"accession": "P12345"/)).toBeInTheDocument();
  expect(invoked).toEqual([{ accession: "P12345" }]);
}, 10_000);

it("starts a metered Pi chat from a tool result", async () => {
  loggedIn("/tools/run/fetch_uniprot");
  const messages: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (path.endsWith("/g")) return json([{ id: "project-alice", name: "个人空间", description: "" }]);
    if (path.endsWith("/c") && init?.method === "POST")
      return json({ id: "session-from-tool", project_id: "project-alice", title: "fetch_uniprot" }, 201);
    if (path.endsWith("/c")) return json([]);
    if (path.endsWith("/mcp/tools")) return json([{ name: "fetch_uniprot", description: "Fetch", input_schema: {
      type: "object", properties: { accession: { type: "string" } }, required: ["accession"],
    } }]);
    if (path.endsWith("/mcp/tools/fetch_uniprot/invoke"))
      return json({ tool: "fetch_uniprot", status: "completed", run_id: "toolrun-1",
        result: { accession: "P12345" } });
    if (path.endsWith("/c/session-from-tool/messages") && init?.method === "POST") {
      messages.push(JSON.parse(String(init.body)));
      return json({ run_id: "run-from-tool" });
    }
    if (path.includes("/runs/run-from-tool/events")) return runEvents();
    if (path.endsWith("/messages") || path.endsWith("/skills") || path.endsWith("/resources")
      || path.endsWith("/tool-runs")) return json([]);
    return json({ detail: "Not found" }, 404);
  }));
  render(<App />);
  const actor = userEvent.setup();
  await actor.type(await screen.findByRole("textbox", { name: "accession" }), "P12345");
  await actor.click(screen.getByRole("button", { name: "运行工具" }));
  await actor.click(await screen.findByRole("button", { name: "交给 Agent 分析" }));

  await waitFor(() => expect(messages).toHaveLength(1));
  expect(JSON.stringify(messages[0])).toContain("P12345");
  expect(window.location.pathname).toBe("/session/session-from-tool");
}, 10_000);

it("does not expose a prediction page without a backing capability", async () => {
  loggedIn("/tools/inabe");
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (path.endsWith("/g")) return json([{ id: "project-alice", name: "我的科研项目", description: "" }]);
    if (path.endsWith("/c") || path.endsWith("/skills") || path.endsWith("/resources") || path.endsWith("/mcp/tools")) return json([]);
    return json({ detail: "Not found" }, 404);
  }));
  render(<App />);
  expect(await screen.findByRole("heading", { name: "未找到页面" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "开始预测" })).not.toBeInTheDocument();
}, 10_000);

it("lists owned artifacts and downloads only those with stored bytes", async () => {
  loggedIn("/artifacts");
  const actor = userEvent.setup();
  const objectUrl = vi.fn(() => "blob:artifact");
  const revoke = vi.fn();
  const NativeURL = URL;
  vi.stubGlobal("URL", class extends NativeURL {
    static createObjectURL = objectUrl;
    static revokeObjectURL = revoke;
  });
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (path.endsWith("/g")) return json([{ id: "project-alice", name: "我的科研项目", description: "" }]);
    if (path.endsWith("/c") || path.endsWith("/skills") || path.endsWith("/resources")) return json([]);
    if (path.endsWith("/artifacts")) return json([
      { id: "result-1", name: "result.cif", kind: "structure", available: true, size: 4, sha256: "abcd" },
      { id: "demo-1", name: "demo.cif", kind: "structure", available: false, size: null, sha256: null },
    ]);
    if (path.endsWith("/artifacts/result-1/download")) return new Response("CIF!", { status: 200 });
    if (path.endsWith("/artifacts/result-1/preview")) return json({
      id: "result-1", name: "result.cif", kind: "structure", text: "CIF!",
    });
    return json({ detail: "Not found" }, 404);
  });
  vi.stubGlobal("fetch", fetcher);
  render(<App />);

  expect(await screen.findByText("result.cif")).toBeInTheDocument();
  expect(screen.getByText("demo.cif")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "下载 result.cif" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "下载 demo.cif" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "预览 result.cif" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "预览 demo.cif" })).toBeDisabled();
  await actor.click(screen.getByRole("button", { name: "预览 result.cif" }));
  expect(await screen.findByText("CIF!")).toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledWith("/api/v1/artifacts/result-1/preview", expect.anything());
  await actor.click(screen.getByRole("button", { name: "关闭预览" }));
  await actor.click(screen.getByRole("button", { name: "下载 result.cif" }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith(
    "/api/v1/artifacts/result-1/download", expect.anything(),
  ));
  expect(objectUrl).toHaveBeenCalledOnce();
  expect(click).toHaveBeenCalledOnce();
  click.mockRestore();
}, 10_000);
