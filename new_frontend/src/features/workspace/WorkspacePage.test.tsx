import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { expect, it, vi } from "vitest";
import { createHttpApi } from "../../api/http";
import type { UserIdentity } from "../../api/types";
import { WorkspacePage } from "./WorkspacePage";

vi.mock("react-resizable-panels", () => ({
  Group: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  Panel: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  Separator: () => <div />,
}));

it("restores the latest waiting run after a page reload", async () => {
  const user: UserIdentity = { id: "alice", email: "alice@example.org", name: "Alice" };
  const projectId = `project-${user.id}`;
  const sessionId = `session-${user.id}`;
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    const json = (value: unknown) => new Response(JSON.stringify(value), { status: 200 });
    if (path.endsWith("/g")) return json([{ id: projectId, name: "来自接口的项目", description: "" }]);
    if (path.endsWith("/c")) return json([{ id: sessionId, project_id: projectId, title: "接口会话", status: "waiting", latest_run_id: "run-recovered" }]);
    if (path.endsWith("/messages")) return json([]);
    if (path.endsWith("/usage")) return json({ tokens: { limit: 100, used: 1, reserved: 0, remaining: 99, unit: "tokens", period: "month", resets_at: "2026-11-01T00:00:00Z" }, gpu: { limit: 60, used: 1, reserved: 2, remaining: 57, unit: "gpu_minutes", period: "day", resets_at: "2026-10-02T00:00:00Z" } });
    if (["/skills", "/resources", "/files", "/artifacts"].some((ending) => path.endsWith(ending))) return json([]);
    if (path.includes("/events")) return new Response(path.includes("after=") ? "" : `data: ${JSON.stringify({ id: "1", run_id: "run-recovered", type: "task.updated", data: { job_id: "job-recovered", label: "Sequence alignment", status: "running", progress: 50 } })}\n\n`, { status: 200 });
    return new Response("Not found", { status: 404 });
  });
  const api = createHttpApi({ baseUrl: "/api/v1", token: () => "test-token", fetcher: fetcher as typeof fetch });
  const query = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={query}>
      <MemoryRouter initialEntries={[`/c/${sessionId}`]}>
        <WorkspacePage api={api} user={user} onLogout={() => {}} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect(await screen.findByText(/Sequence alignment · 50%/)).toBeInTheDocument();
  expect(fetcher.mock.calls.every(([input]) => !String(input).includes("/af3/jobs/"))).toBe(true);
  expect(screen.getAllByText("来自接口的项目").length).toBeGreaterThan(0);
  expect(screen.queryByText("AF3 MOCK")).not.toBeInTheDocument();
  await userEvent.setup().click(screen.getByRole("link", { name: "技能" }));
  expect(await screen.findByText("暂无可用技能")).toBeInTheDocument();
});

it("uses the project in the URL and shows an honest empty session state", async () => {
  const user: UserIdentity = { id: "alice", email: "alice@example.org", name: "Alice" };
  const requestedSessions: string[] = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    const json = (value: unknown) => new Response(JSON.stringify(value), { status: 200 });
    if (path.endsWith("/g")) return json([
      { id: "project-a", name: "项目 A", description: "" },
      { id: "project-b", name: "项目 B", description: "" },
    ]);
    if (path.endsWith("/c")) { requestedSessions.push(path); return json([]); }
    if (path.endsWith("/usage")) return json({ tokens: { limit: 100, used: 0, reserved: 0, remaining: 100, unit: "tokens", period: "month", resets_at: "2026-11-01T00:00:00Z" }, gpu: { limit: 60, used: 0, reserved: 0, remaining: 60, unit: "gpu_minutes", period: "day", resets_at: "2026-10-02T00:00:00Z" } });
    if (["/skills", "/resources", "/files", "/artifacts"].some((ending) => path.endsWith(ending))) return json([]);
    return new Response("Not found", { status: 404 });
  });
  const api = createHttpApi({ baseUrl: "/api/v1", token: () => "test-token", fetcher: fetcher as typeof fetch });
  const query = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={query}><MemoryRouter initialEntries={["/p/project-b"]}><WorkspacePage api={api} user={user} onLogout={() => {}} /></MemoryRouter></QueryClientProvider>);

  expect(await screen.findByText("当前项目暂无会话")).toBeInTheDocument();
  expect(requestedSessions).toContain("/api/v1/g/g-p-b/c");
  expect(screen.getByRole("button", { name: "项目 B" })).toHaveAttribute("aria-current", "page");
  expect(screen.queryByRole("button", { name: "打开研究会话" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "帮助" })).not.toBeInTheDocument();
});
