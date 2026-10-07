import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "./App";
import { useComposerStore } from "../features/chat/composerStore";

vi.mock("react-resizable-panels", () => ({
  Group: ({ children, className }: { children: React.ReactNode; className?: string }) => <div className={className}>{children}</div>,
  Panel: ({ children, className }: { children: React.ReactNode; className?: string }) => <div className={className}>{children}</div>,
  Separator: () => <div />,
}));

afterEach(() => {
  cleanup();
  useComposerStore.getState().clear();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  window.history.replaceState(null, "", "/");
});

it("renders catalog data from HTTP and submits selected context as API refs", async () => {
  window.localStorage.clear();
  const sent: unknown[] = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const json = (value: unknown) => new Response(JSON.stringify(value), { status: 200 });
    if (path.endsWith("/auth/demo")) return json({ access_token: "token-alice", user: { id: "alice", email: "alice@example.org", name: "Alice" } });
    if (path.endsWith("/g")) return json([{ id: "project-alice", name: "来自接口的项目", description: "" }]);
    if (path.endsWith("/c")) return json([{ id: "session-alice", project_id: "project-alice", title: "接口会话", status: "idle" }]);
    if (path.endsWith("/skills")) return json([{ id: "skill-api", name: "Genome scan", description: "" }]);
    if (path.endsWith("/resources")) return json([{ id: "resource-api", name: "Reference database", description: "" }]);
    if (path.endsWith("/files") || path.endsWith("/artifacts")) return json([]);
    if (path.endsWith("/usage")) return json({ tokens: { limit: 1000, used: 1, reserved: 0, remaining: 999, unit: "tokens", period: "month", resets_at: "2026-11-01T00:00:00Z" }, gpu: { limit: 60, used: 0, reserved: 0, remaining: 60, unit: "gpu_minutes", period: "day", resets_at: "2026-10-02T00:00:00Z" } });
    if (path.endsWith("/messages") && init?.method === "POST") {
      sent.push(JSON.parse(String(init.body)));
      return json({ run_id: "run-api" });
    }
    if (path.endsWith("/messages")) return json([]);
    if (path.includes("/events")) return new Response(`data: ${JSON.stringify({ id: "1", run_id: "run-api", type: "task.updated", data: { job_id: "job-api", label: "Genome scan", status: "running", progress: 45 } })}\n\n`, { status: 200 });
    return new Response("Not found", { status: 404 });
  });
  vi.stubGlobal("fetch", fetcher);

  const user = userEvent.setup();
  render(<App />);
  await user.type(await screen.findByLabelText("邮箱"), "alice@example.org");
  await user.click(screen.getByRole("button", { name: "进入演示工作区" }));
  expect(await screen.findByRole("heading", { name: "今天有什么可以帮你？" })).toBeInTheDocument();
  await user.click(await screen.findByRole("link", { name: "接口会话" }));
  expect(screen.queryByText(/Agent 模式/)).not.toBeInTheDocument();
  await user.type(await screen.findByLabelText("消息内容", {}, { timeout: 5_000 }), "/");
  await user.click(screen.getByRole("button", { name: "Genome scan" }));
  await user.type(screen.getByLabelText("消息内容"), "@");
  await user.click(screen.getByRole("button", { name: "Reference database" }));
  await user.type(screen.getByLabelText("消息内容"), "Analyze sample");
  await user.click(screen.getByRole("button", { name: "发送消息" }));

  await waitFor(() => expect(sent).toEqual([{
    content: "Analyze sample", attachments: [],
    skills: [{ id: "skill-api", name: "Genome scan" }],
    resources: [{ id: "resource-api", name: "Reference database" }],
  }]));
  expect(await screen.findByText(/Genome scan · 45%/)).toBeInTheDocument();
}, 10_000);

it("upgrades a mock guest without changing ownership or losing its session", async () => {
  const json = (value: unknown) => new Response(JSON.stringify(value), { status: 200 });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/auth/csrf")) return new Response(null, { status: 204 });
    if (path.endsWith("/auth/anonymous")) return json({ access_token: "guest-jwt", expires_in: 3600,
      user: { id: "guest-1", email: "", name: "Guest", is_anonymous: true } });
    if (path.endsWith("/auth/upgrade/email")) return json({ status: "check_email" });
    if (path.endsWith("/auth/upgrade/email/verify")) return json({ access_token: "member-jwt", expires_in: 3600,
      user: { id: "guest-1", email: "new@example.org", name: "New", is_anonymous: false } });
    if (path.endsWith("/g")) return json([{ id: "project-guest-1", name: "Personal", description: "" }]);
    if (path.endsWith("/c") || path.endsWith("/skills") || path.endsWith("/resources") || path.endsWith("/usage/entries")) return json([]);
    if (path.endsWith("/usage")) return json({ tokens: { limit: 20000, used: 0, reserved: 0, remaining: 20000, unit: "tokens", period: "month", resets_at: "2026-11-01T00:00:00Z" }, gpu: { limit: 0, used: 0, reserved: 0, remaining: 0, unit: "gpu_minutes", period: "day", resets_at: "2026-10-03T00:00:00Z" } });
    return json({ detail: "Not found" });
  }));
  render(<App />);
  const actor = userEvent.setup();
  await actor.click(await screen.findByRole("button", { name: "先以游客身份体验" }));
  await actor.click(await screen.findByRole("button", { name: "账号菜单：Guest" }));
  await actor.click(screen.getByRole("menuitem", { name: "设置" }));
  expect(await screen.findByText("游客可聊天和上传少量文件；GPU 与 AF3 任务需要升级账号。")).toBeInTheDocument();
  await actor.type(await screen.findByLabelText("升级邮箱"), "new@example.org");
  await actor.click(screen.getByRole("button", { name: "发送验证码" }));
  await actor.type(screen.getByLabelText("邮件验证码"), "123456");
  await actor.click(screen.getByRole("button", { name: "验证并升级" }));

  await waitFor(() => expect(window.localStorage.getItem("research_access_token")).toBe("member-jwt"));
  expect(screen.getByText(/new@example.org/)).toBeInTheDocument();
});

it("restores the rotated mock session after a Google upgrade callback", async () => {
  window.localStorage.setItem("research_access_token", "old-guest-jwt");
  window.history.replaceState(null, "", "/auth/callback");
  const json = (value: unknown) => new Response(JSON.stringify(value), { status: 200 });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/auth/csrf")) return json({ csrf_token: "signed-csrf" });
    if (path.endsWith("/auth/refresh")) return json({ access_token: "linked-jwt", expires_in: 3600,
      user: { id: "guest-1", email: "linked@example.org", name: "Linked", is_anonymous: false } });
    if (path.endsWith("/me")) return json({ id: "guest-1", email: "", name: "Guest", is_anonymous: true });
    if (path.endsWith("/g")) return json([{ id: "project-guest-1", name: "Personal", description: "" }]);
    if (["/c", "/skills", "/resources"].some((ending) => path.endsWith(ending))) return json([]);
    return json({ detail: "Not found" });
  }));

  render(<App />);
  await waitFor(() => expect(window.localStorage.getItem("research_access_token")).toBe("linked-jwt"));
  await waitFor(() => expect(window.location.pathname).toBe("/"));
});

it("keeps a guest signed in and explains a Google identity conflict", async () => {
  window.localStorage.setItem("research_access_token", "guest-jwt");
  window.history.replaceState(null, "", "/auth/callback?error=GOOGLE_IDENTITY_CONFLICT");
  const json = (value: unknown) => new Response(JSON.stringify(value), { status: 200 });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/auth/csrf")) return json({ csrf_token: "signed-csrf" });
    if (path.endsWith("/auth/refresh")) return json({ access_token: "guest-jwt", expires_in: 3600,
      user: { id: "guest-1", email: "", name: "Guest", is_anonymous: true } });
    if (path.endsWith("/g")) return json([{ id: "project-guest-1", name: "Personal", description: "" }]);
    if (["/c", "/skills", "/resources"].some((ending) => path.endsWith(ending))) return json([]);
    return json({ detail: "Not found" });
  }));

  render(<App />);
  expect(await screen.findByRole("alert")).toHaveTextContent("这个 Google 账号已绑定其他用户，游客数据仍保留。");
  expect(screen.getByRole("link", { name: "返回设置" })).toHaveAttribute("href", "/settings");
  expect(window.localStorage.getItem("research_access_token")).toBe("guest-jwt");
});

it("warns before discarding a mock guest and revokes its server session", async () => {
  window.localStorage.setItem("research_access_token", "guest-jwt");
  const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/auth/csrf")) return new Response(JSON.stringify({ csrf_token: "signed-csrf" }), { status: 200 });
    if (path.endsWith("/me")) return new Response(JSON.stringify({ id: "guest-1", email: "", name: "Guest", is_anonymous: true }), { status: 200 });
    if (path.endsWith("/auth/logout")) return new Response(null, { status: 204 });
    if (path.endsWith("/g")) return new Response(JSON.stringify([{ id: "project-guest-1", name: "Personal", description: "" }]), { status: 200 });
    return new Response("[]", { status: 200 });
  });
  vi.stubGlobal("fetch", fetcher);
  render(<App />);
  const actor = userEvent.setup();
  await actor.click(await screen.findByRole("button", { name: "账号菜单：Guest" }));
  await actor.click(screen.getByRole("menuitem", { name: "退出登录" }));
  expect(confirm).toHaveBeenCalledWith("游客会话退出后将无法找回。确定退出吗？");
  expect(window.localStorage.getItem("research_access_token")).toBe("guest-jwt");
  expect(fetcher.mock.calls.some(([input]) => String(input).endsWith("/auth/logout"))).toBe(false);

  await actor.click(screen.getByRole("button", { name: "账号菜单：Guest" }));
  await actor.click(screen.getByRole("menuitem", { name: "退出登录" }));
  await waitFor(() => expect(window.localStorage.getItem("research_access_token")).toBeNull());
  expect(fetcher.mock.calls.some(([input]) => String(input).endsWith("/auth/logout"))).toBe(true);
});

it("creates a personal session on the first message and sends that message once", async () => {
  window.localStorage.setItem("research_access_token", "stored-token");
  const requests: { path: string; method: string; body?: unknown }[] = [];
  const json = (value: unknown) => new Response(JSON.stringify(value), { status: 200 });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    if (method !== "GET") requests.push({ path, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (path.endsWith("/me")) return json({ id: "alice", email: "alice@example.org", name: "Alice" });
    if (path.endsWith("/g") && method === "GET") return json([{ id: "project-alice", name: "我的科研项目", description: "" }]);
    if (path.endsWith("/c") && method === "POST") return json({ id: "session-first", project_id: "project-alice", title: "分析 P53 结构", status: "idle" });
    if (path.endsWith("/c") && method === "GET") return json([]);
    if (path.endsWith("/c/session-first/messages") && method === "POST") return json({ run_id: "run-first" });
    if (path.endsWith("/messages") && method === "GET") return json([]);
    if (path.includes("/events")) return new Response("", { status: 200 });
    if (["/skills", "/resources", "/files", "/artifacts"].some((ending) => path.endsWith(ending))) return json([]);
    return new Response("Not found", { status: 404 });
  }));

  render(<App />);
  const actor = userEvent.setup();
  await actor.type(await screen.findByLabelText("消息内容"), "分析 P53 结构");
  await actor.click(screen.getByRole("button", { name: "发送消息" }));

  await waitFor(() => expect(requests.filter((request) => request.path.endsWith("/c") && request.method === "POST")).toHaveLength(1));
  await waitFor(() => expect(requests.filter((request) => request.path.endsWith("/c/session-first/messages") && request.method === "POST")).toHaveLength(1));
  expect(requests.at(-1)?.body).toMatchObject({ content: "分析 P53 结构", skills: [] });
  expect(window.location.pathname).toBe("/session/session-first");
}, 10_000);

it("creates a project conversation at the project conversation URL", async () => {
  window.localStorage.setItem("research_access_token", "stored-token");
  window.history.replaceState(null, "", "/p/project-lab/new");
  const json = (value: unknown) => new Response(JSON.stringify(value), { status: 200 });
  let projectSessions: { id: string; project_id: string; title: string }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ id: "alice", email: "alice@example.org", name: "Alice" });
    if (path.endsWith("/g")) return json([{ id: "project-alice", name: "个人", description: "" }, { id: "project-lab", name: "实验", description: "" }]);
    if (path.endsWith("/g/g-p-lab/c") && init?.method === "POST") {
      const created = { id: "session-lab", project_id: "project-lab", title: "分析结构" };
      projectSessions = [created];
      return json(created);
    }
    if (path.endsWith("/g/g-p-lab/c")) return json(projectSessions);
    if (path.endsWith("/c")) return json([]);
    if (path.endsWith("/g/g-p-lab/skills")) return json({ skill_ids: [], default_skill_ids: [] });
    if (path.endsWith("/g/g-p-lab/c/session-lab/messages") && init?.method === "POST") return json({ run_id: "run-lab" });
    if (path.endsWith("/messages") || path.endsWith("/skills") || path.endsWith("/resources")) return json([]);
    if (path.includes("/events")) return new Response("", { status: 200 });
    return json({ detail: "Not found" });
  }));
  render(<App />);
  const actor = userEvent.setup();
  await actor.type(await screen.findByLabelText("消息内容"), "分析结构");
  await actor.click(screen.getByRole("button", { name: "发送消息" }));
  await waitFor(() => expect(window.location.pathname).toBe("/p/project-lab/c/session-lab"));
}, 10_000);

it("restores a deep linked session after checking the stored API token", async () => {
  window.localStorage.setItem("research_access_token", "stored-token");
  window.history.replaceState(null, "", "/session/session-alice");
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    const json = (value: unknown) => new Response(JSON.stringify(value), { status: 200 });
    if (path.endsWith("/me")) return json({ id: "alice", email: "alice@example.org", name: "Alice" });
    if (path.endsWith("/g")) return json([{ id: "project-alice", name: "接口项目", description: "" }]);
    if (path.endsWith("/c")) return json([{ id: "session-alice", project_id: "project-alice", title: "接口会话", status: "waiting", latest_run_id: "run-alice" }]);
    if (path.endsWith("/messages")) return json([]);
    if (path.includes("/events")) return new Response(`data: ${JSON.stringify({ id: "1", run_id: "run-alice", type: "task.updated", data: { job_id: "job-alice", label: "后台计算", status: "running", progress: 30 } })}\n\n`, { status: 200 });
    if (path.endsWith("/usage")) return json({ tokens: { limit: 100, used: 0, reserved: 0, remaining: 100, unit: "tokens", period: "month", resets_at: "2026-11-01T00:00:00Z" }, gpu: { limit: 60, used: 0, reserved: 0, remaining: 60, unit: "gpu_minutes", period: "day", resets_at: "2026-10-02T00:00:00Z" } });
    if (["/skills", "/resources", "/files", "/artifacts"].some((ending) => path.endsWith(ending))) return json([]);
    return new Response("Not found", { status: 404 });
  });
  vi.stubGlobal("fetch", fetcher);

  render(<App />);
  expect(await screen.findByText(/后台计算 · 30%/)).toBeInTheDocument();
  await waitFor(() => expect(window.location.pathname).toBe("/session/session-alice"));
}, 10_000);

it("opens a project conversation at its own short URL", async () => {
  window.localStorage.setItem("research_access_token", "stored-token");
  window.history.replaceState(null, "", "/p/project-lab/c/session-lab");
  const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ id: "alice", email: "alice@example.org", name: "Alice" });
    if (path.endsWith("/g")) return json([{ id: "project-alice", name: "个人", description: "" }, { id: "project-lab", name: "实验", description: "" }]);
    if (path.endsWith("/g/g-p-lab/c")) return json([{ id: "session-lab", project_id: "project-lab", title: "蛋白预测" }]);
    if (path.endsWith("/c")) return json([]);
    if (path.endsWith("/g/g-p-lab/skills")) return json({ skill_ids: [], default_skill_ids: [] });
    if (path.endsWith("/messages") || path.endsWith("/skills") || path.endsWith("/resources")) return json([]);
    return json({ detail: "Not found" }, 404);
  }));
  render(<App />);
  expect(await screen.findByRole("link", { name: "蛋白预测" })).toHaveAttribute("aria-current", "page");
  expect(window.location.pathname).toBe("/p/project-lab/c/session-lab");
}, 10_000);

it("does not redirect a project chat URL after that chat moves to personal", async () => {
  window.localStorage.setItem("research_access_token", "stored-token");
  window.history.replaceState(null, "", "/p/project-lab/c/session-moved");
  const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ id: "alice", email: "alice@example.org", name: "Alice" });
    if (path.endsWith("/g")) return json([{ id: "project-alice", name: "个人", description: "" }, { id: "project-lab", name: "实验", description: "" }]);
    if (path.endsWith("/g/g-p-lab/c")) return json([]);
    if (path.endsWith("/c")) return json([{ id: "session-moved", project_id: "project-alice", title: "已移回个人" }]);
    if (path.endsWith("/g/g-p-lab/c/session-moved")) return json({ detail: "Not found" }, 404);
    if (path.endsWith("/g/g-p-lab/skills")) return json({ skill_ids: [], default_skill_ids: [] });
    if (path.endsWith("/messages") || path.endsWith("/skills") || path.endsWith("/resources")) return json([]);
    return json({ detail: "Not found" }, 404);
  });
  vi.stubGlobal("fetch", fetcher);
  render(<App />);
  expect(await screen.findByRole("heading", { name: "未找到页面" })).toBeInTheDocument();
  expect(window.location.pathname).toBe("/p/project-lab/c/session-moved");
  expect(fetcher.mock.calls.some(([input]) => String(input).endsWith("/g/g-p-lab/c/session-moved"))).toBe(true);
}, 10_000);

it("opens a personal new chat instead of a project dashboard", async () => {
  window.localStorage.setItem("research_access_token", "stored-token");
  const json = (value: unknown) => new Response(JSON.stringify(value), { status: 200 });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ id: "alice", email: "alice@example.org", name: "Alice" });
    if (path.endsWith("/g")) return json([{ id: "project-alice", name: "我的科研项目", description: "" }]);
    if (path.endsWith("/c")) return json([{ id: "session-alice", project_id: "project-alice", title: "蛋白质结合位点分析", status: "idle" }]);
    if (["/skills", "/resources", "/files", "/artifacts"].some((ending) => path.endsWith(ending))) return json([]);
    if (path.endsWith("/usage")) return json({ tokens: { limit: 100, used: 0, reserved: 0, remaining: 100, unit: "tokens", period: "month", resets_at: "2026-11-01T00:00:00Z" }, gpu: { limit: 60, used: 0, reserved: 0, remaining: 60, unit: "gpu_minutes", period: "day", resets_at: "2026-10-02T00:00:00Z" } });
    return new Response("Not found", { status: 404 });
  }));

  render(<App />);
  expect(await screen.findByRole("heading", { name: "今天有什么可以帮你？" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "新聊天" })).toHaveAttribute("aria-current", "page");
  await userEvent.setup().click(await screen.findByRole("link", { name: "蛋白质结合位点分析" }));
  expect(window.location.pathname).toBe("/session/session-alice");
  expect(screen.getByRole("link", { name: "蛋白质结合位点分析" })).toHaveAttribute("aria-current", "page");
}, 10_000);

it.each(["/chats/session-alice", "/p/project-alice/c/session-alice"])("does not serve removed chat URL %s", async (oldPath) => {
  window.localStorage.setItem("research_access_token", "stored-token");
  window.history.replaceState(null, "", `${oldPath}?tab=messages#last`);
  const json = (value: unknown) => new Response(JSON.stringify(value), { status: 200 });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ id: "alice", email: "alice@example.org", name: "Alice" });
    if (path.endsWith("/g")) return json([{ id: "project-alice", name: "我的科研项目", description: "" }]);
    if (path.endsWith("/c")) return json([{ id: "session-alice", project_id: "project-alice", title: "旧对话" }]);
    if (path.endsWith("/messages") || path.endsWith("/skills") || path.endsWith("/resources")) return json([]);
    return json({ detail: "Not found" });
  }));
  render(<App />);
  expect(await screen.findByRole("heading", { name: "未找到页面" })).toBeInTheDocument();
  expect(window.location.pathname).toBe(oldPath);
  expect(window.location.search).toBe("?tab=messages");
  expect(window.location.hash).toBe("#last");
}, 10_000);
