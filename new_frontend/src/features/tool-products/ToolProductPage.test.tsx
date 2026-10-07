import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import type { PublishedToolProduct, ToolRunEvent, ToolRunSnapshot } from "../../api/generated";
import type { ResearchApi } from "../../api/types";
import { ApiError } from "../../api/http";
import { App } from "../../app/App";
import { LanguageProvider } from "../../i18n/LanguageProvider";
import { ToolProductPage } from "./ToolProductPage";

vi.mock("../mono/MolstarCanvas", () => ({ default: () => <div data-testid="molstar" /> }));

const text = (en: string, zh: string) => ({ en, "zh-CN": zh });
const product: PublishedToolProduct = {
  release_id: "release-1", product_id: "product-flex", slug: "flex-design", revision: 1,
  title: text("Flexible Design", "灵活设计"), description: text("A published scientific workflow", "已发布的科研流程"),
  state: "published", published_at: "2026-10-06T00:00:00Z",
  actions: [{ id: "generate", label: text("Generate", "生成"), kind: "capability", binding_ids: ["binding-1"], input_schema: {
    type: "object", properties: { target: { type: "string" } }, required: ["target"], additionalProperties: false,
  } }],
  ui_schema: {
    schema_version: "pskit.tool-ui.v1",
    product: { slug: "flex-design", title: text("Flexible Design", "灵活设计"), description: text("A published scientific workflow", "已发布的科研流程") },
    page: { layout: "split-workspace", input_width: 5, result_width: 7 }, state: { mode: { initial: "one_shot" } },
    sections: [{ id: "input", title: text("Input", "输入"), fields: [
      { id: "target", component: "protein-input", label: text("Protein target", "蛋白质目标"), input_pointer: "/form/target", required: true },
    ] }],
    actions: [{ id: "run", label: text("Run design", "运行设计"), target: { action_id: "generate" } }],
    result_views: [
      { id: "stages", component: "stage-flow", source: "/run" },
      { id: "sequences", component: "sequence-table", source: "/run/result/sequences", title: text("Candidates", "候选序列"), preview_limit: 1 },
      { id: "artifacts", component: "artifact-list", source: "/run/artifacts", title: text("Outputs", "产物"), preview_limit: 1 },
    ],
    handoffs: [{ id: "analyze", label: text("Analyze with Agent", "交给 Agent 分析"), summary_source: "/run/result/summary", artifact_sources: ["/run/artifacts"] }],
  },
};

const baseRun: ToolRunSnapshot = {
  run_id: "run-1", product_slug: "flex-design", release_id: "release-1", action_id: "generate", user_id: "alice",
  status: "completed", progress: 100, result: { summary: "Two candidates", sequences: [{ id: "one", sequence: "ACGU" }, { id: "two", sequence: "GGCA" }], private_remote_data: "do-not-forward" },
  artifacts: [{ id: "artifact-1", name: "all-candidates.csv", kind: "csv", available: true, size: 20 }],
  usage: { wall_ms: 1200, gpu_device_ms: 900, source: "service_reported" }, created_at: "2026-10-06T00:00:00Z", updated_at: "2026-10-06T00:01:00Z",
};

const stageEvents: ToolRunEvent[] = [
  { event_id: "event-1", run_id: "run-1", sequence: 1, type: "stage.started", data: { job_id: "job-1", label: "Generate candidates" }, created_at: "2026-10-06T00:00:01Z" },
  { event_id: "event-2", run_id: "run-1", sequence: 2, type: "stage.progress", data: { job_id: "job-1", progress: 40 }, created_at: "2026-10-06T00:00:02Z" },
];

function apiFixture(overrides: Partial<ResearchApi> = {}): ResearchApi {
  return {
    getToolProduct: vi.fn(async () => product),
    getToolProductRuns: vi.fn(async () => [baseRun]),
    startToolProductRun: vi.fn(async () => baseRun),
    getToolProductRun: vi.fn(async () => baseRun),
    getToolProductRunEvents: vi.fn(async () => stageEvents),
    cancelToolProductRun: vi.fn(async () => ({ ...baseRun, status: "cancelling" })),
    handoffToolProductRun: vi.fn(async () => ({ run_id: "run-1", handoff_id: "analyze", summary: "Bounded server summary", artifacts: baseRun.artifacts })),
    createSession: vi.fn(async () => ({ id: "session-agent", project_id: "project-alice", title: "Flexible Design" })),
    sendMessage: vi.fn(async () => ({ run_id: "agent-run" })),
    uploadFile: vi.fn(),
    ...overrides,
  } as unknown as ResearchApi;
}

function renderPage(api: ResearchApi, initial = "/tools/flex-design") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><LanguageProvider><MemoryRouter initialEntries={[initial]}>
    <Routes><Route path="/tools/:slug" element={<ToolProductPage api={api} slug="flex-design" userId="alice" theme="light" onBack={vi.fn()} />} />
      <Route path="/session/:id" element={<div>Agent conversation</div>} /></Routes>
  </MemoryRouter></LanguageProvider></QueryClientProvider>);
}

afterEach(() => {
  cleanup(); localStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); window.history.replaceState(null, "", "/");
});

it("adds a published product to the directory and opens its full Tools route without a product branch", async () => {
  localStorage.setItem("research_access_token", "demo-token");
  localStorage.setItem("research_language", "en");
  window.history.replaceState(null, "", "/tools");
  const requested: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://test"); requested.push(url.pathname);
    if (url.pathname.endsWith("/me")) return Response.json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (url.pathname === "/api/v1/tool-products") return Response.json({ items: [product], next_cursor: null });
    if (url.pathname === "/api/v1/tool-products/flex-design") return Response.json(product);
    if (url.pathname.endsWith("/mcp/tools") || url.pathname.endsWith("/g") || url.pathname.endsWith("/c") || url.pathname.endsWith("/skills") || url.pathname.endsWith("/resources")) return Response.json([]);
    return Response.json([], { status: 200 });
  }));
  render(<App />);
  const actor = userEvent.setup();
  const card = await screen.findByRole("link", { name: /Flexible Design/ });
  expect(card).toHaveAttribute("href", "/tools/flex-design");
  await actor.click(card);
  const page = await screen.findByRole("article", { name: "Flexible Design" });
  const header = document.querySelector<HTMLElement>(".mono-topbar")!;
  expect(within(header).getByRole("heading", { name: "Flexible Design", level: 1 })).toBeInTheDocument();
  expect(within(header).getByText("A published scientific workflow")).toBeInTheDocument();
  expect(within(header).getByRole("button", { name: "Back to tools" })).toBeInTheDocument();
  expect(within(page).queryByRole("heading", { name: "Flexible Design" })).not.toBeInTheDocument();
  expect(screen.getAllByRole("heading", { name: "Flexible Design" })).toHaveLength(1);
  await actor.click(within(page).getByRole("tab", { name: "Run history" }));
  await waitFor(() => expect(requested).toContain("/api/v1/tool-products/flex-design/runs"));
});

it("starts a pending run, recovers real events by cursor, labels usage source and keeps Stop until terminal", async () => {
  localStorage.setItem("research_language", "en");
  let snapshotCalls = 0;
  const getRun = vi.fn(async () => {
    snapshotCalls += 1;
    return snapshotCalls === 1 ? { ...baseRun, status: "running" as const, progress: 40 } : baseRun;
  });
  const eventCursors: number[] = [];
  const getEvents = vi.fn(async (_id: string, cursor: number) => {
    eventCursors.push(cursor);
    return cursor === 0 ? stageEvents : [{ ...stageEvents[1], event_id: "event-3", sequence: 3, type: "stage.completed" as const, data: { job_id: "job-1", status: "completed" } }];
  });
  const api = apiFixture({
    startToolProductRun: vi.fn(async () => ({ ...baseRun, status: "queued" as const, progress: 0, result: null, artifacts: [], usage: null })),
    getToolProductRun: getRun, getToolProductRunEvents: getEvents,
    cancelToolProductRun: vi.fn(async () => ({ ...baseRun, status: "cancelling" as const, progress: 40 })),
  });
  renderPage(api);
  const actor = userEvent.setup();
  await actor.type(await screen.findByRole("textbox", { name: "Protein target" }), "1A9N");
  await actor.click(screen.getByRole("button", { name: "Run design" }));
  expect(api.startToolProductRun).toHaveBeenCalledWith(
    "flex-design", "generate", { target: "1A9N" }, expect.any(String),
  );
  expect(await screen.findByText("Generate candidates")).toBeInTheDocument();
  expect(screen.getByText(/service reported/i)).toBeInTheDocument();
  expect(screen.queryByText("GGCA")).not.toBeInTheDocument();
  expect(screen.getByText("all-candidates.csv")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Stop run" })).toBeInTheDocument();
  await actor.click(screen.getByRole("button", { name: "Stop run" }));
  expect(screen.getByRole("button", { name: "Stopping…" })).toBeDisabled();
  await waitFor(() => expect(eventCursors).toContain(2), { timeout: 3_000 });
  await waitFor(() => expect(screen.queryByRole("button", { name: "Stop run" })).not.toBeInTheDocument(), { timeout: 3_000 });
});

it("recovers a refreshed run from cursor zero and scopes history to the exact product", async () => {
  localStorage.setItem("research_language", "en");
  const api = apiFixture();
  renderPage(api, "/tools/flex-design?run=run-1");
  expect(await screen.findByText("ACGU")).toBeInTheDocument();
  expect(api.getToolProductRun).toHaveBeenCalledWith("run-1");
  expect(api.getToolProductRunEvents).toHaveBeenCalledWith("run-1", 0);
  await userEvent.click(screen.getByRole("tab", { name: "Run history" }));
  expect(await screen.findByRole("button", { name: /run-1/ })).toBeInTheDocument();
  expect(api.getToolProductRuns).toHaveBeenCalledWith("flex-design");
});

it("explains a CPU quota rejection and preserves the entered inputs for retry", async () => {
  localStorage.setItem("research_language", "en");
  const api = apiFixture({ startToolProductRun: vi.fn(async () => {
    throw new ApiError(429, { code: "CPU_QUOTA_EXCEEDED" });
  }) });
  renderPage(api);
  const actor = userEvent.setup();
  const input = await screen.findByRole("textbox", { name: "Protein target" });
  await actor.type(input, "1A9N");
  await actor.click(screen.getByRole("button", { name: "Run design" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Your daily CPU quota is insufficient. Check your usage or contact an administrator.");
  expect(screen.queryByText("The run could not start. Check the inputs and quota, then try again.")).not.toBeInTheDocument();
  expect(input).toHaveValue("1A9N");
  expect(screen.getByRole("button", { name: "Run design" })).toBeEnabled();
  expect(api.getToolProductRun).not.toHaveBeenCalled();
});

it.each([
  { language: "zh", code: "CPU_QUOTA_EXCEEDED", message: "今日 CPU 额度不足，请检查用量或联系管理员调整额度。" },
  { language: "en", code: "GPU_QUOTA_EXCEEDED", message: "Your daily GPU quota is exhausted." },
])("shows the resource-specific rejection ($language, $code)", async ({ language, code, message }) => {
  localStorage.setItem("research_language", language);
  const api = apiFixture({ startToolProductRun: vi.fn(async () => {
    throw new ApiError(429, { code, message: "Private upstream diagnostic" });
  }) });
  renderPage(api);
  const actor = userEvent.setup();
  await actor.type(await screen.findByRole("textbox", { name: language === "en" ? "Protein target" : "蛋白质目标" }), "1A9N");
  await actor.click(screen.getByRole("button", { name: language === "en" ? "Run design" : "运行设计" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(message);
  expect(screen.queryByText("Private upstream diagnostic")).not.toBeInTheDocument();
});

it("hands off only the immutable run context returned by Python", async () => {
  localStorage.setItem("research_language", "en");
  const api = apiFixture();
  renderPage(api, "/tools/flex-design?run=run-1");
  const actor = userEvent.setup();
  await actor.click(await screen.findByRole("button", { name: "Analyze with Agent" }));
  expect(await screen.findByText("Agent conversation")).toBeInTheDocument();
  expect(api.handoffToolProductRun).toHaveBeenCalledWith("run-1", "analyze");
  const message = vi.mocked(api.sendMessage).mock.calls[0][1];
  expect(message.attachments).toEqual([{ id: "artifact-1", name: "all-candidates.csv" }]);
  expect(message.content).toContain("run-1");
  expect(message.content).toContain("Bounded server summary");
  expect(message.content).not.toContain("do-not-forward");
  expect(message.content.length).toBeLessThan(5_000);
});
