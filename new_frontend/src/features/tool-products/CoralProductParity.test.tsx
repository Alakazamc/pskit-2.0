import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import type { PublishedToolProduct, ToolRunSnapshot } from "../../api/generated";
import type { ResearchApi } from "../../api/types";
import { LanguageProvider } from "../../i18n/LanguageProvider";
import { ToolProductPage } from "./ToolProductPage";

const text = (en: string, zh: string) => ({ en, "zh-CN": zh });
const coralProduct = {
  release_id: "release-coral", product_id: "product-coral", slug: "coral", revision: 1,
  title: text("CORAL Scientific Workflows", "CORAL 科研工作流"),
  description: text("Reviewed CORAL services", "经过审查的 CORAL 服务"),
  state: "published", published_at: "2026-10-06T00:00:00Z",
  actions: [
    { id: "coral-one-shot", label: text("One-shot generation", "一次生成"), kind: "capability", binding_ids: ["binding-one"], input_schema: {
      type: "object", properties: {
        pdb_id: { type: "string" }, chain: { type: "string" },
        num_samples: { type: "integer", minimum: 1, maximum: 10 },
        length: { type: "integer", minimum: 10, maximum: 500 },
      }, required: ["pdb_id", "chain", "num_samples", "length"], additionalProperties: false,
    } },
    { id: "coral-pocket", label: text("Pocket optimization", "口袋优化"), kind: "capability", binding_ids: ["binding-pocket"], input_schema: {
      type: "object", properties: { binding_domain_file: { type: "string" }, checkpoint_path: { type: "string" }, per_sample: { type: "integer" } }, additionalProperties: false,
    } },
  ],
  ui_schema: {
    schema_version: "pskit.tool-ui.v1",
    product: { slug: "coral", title: text("CORAL Scientific Workflows", "CORAL 科研工作流"), description: text("Reviewed CORAL services", "经过审查的 CORAL 服务") },
    page: { layout: "split-workspace", input_width: 5, result_width: 7 },
    state: { mode: { initial: "one_shot" } },
    sections: [
      { id: "mode", title: text("Workflow", "工作流"), fields: [{ id: "mode-choice", component: "segmented-control", label: text("Mode", "模式"), input_pointer: "/form/mode", required: true, options: [
        { value: "one_shot", label: text("One-shot", "一次生成") },
        { value: "pocket", label: text("Pocket", "口袋分析") },
      ] }] },
      { id: "one-shot", title: text("Protein target", "蛋白质目标"), visible_when: { source: "/form/mode", equals: "one_shot" }, fields: [
        { id: "pdb", component: "protein-input", label: text("PDB ID", "PDB 编号"), input_pointer: "/form/pdb_id", required: true },
        { id: "chain", component: "text-input", label: text("Chain", "蛋白链"), input_pointer: "/form/chain", required: true },
        { id: "samples", component: "number-input", label: text("Candidate count", "候选数量"), input_pointer: "/form/num_samples", default: 1, minimum: 1, maximum: 10, step: 1 },
        { id: "length", component: "number-input", label: text("RNA length", "RNA 长度"), input_pointer: "/form/length", default: 50, minimum: 10, maximum: 500, step: 1 },
      ] },
      { id: "pocket", title: text("Pocket optimization", "口袋优化"), visible_when: { source: "/form/mode", equals: "pocket" }, fields: [
        { id: "binding", component: "text-input", label: text("Binding-domain file", "结合域文件"), input_pointer: "/form/binding_domain_file", required: true },
      ] },
    ],
    actions: [{ id: "run", label: text("Run CORAL", "运行 CORAL"), target: { by_state: { source: "/form/mode", map: { one_shot: "coral-one-shot", pocket: "coral-pocket" } } } }],
    result_views: [
      { id: "stages", component: "stage-flow", source: "/run/status", title: text("Progress", "运行进度") },
      { id: "summary", component: "metric-grid", source: "/run/result", title: text("Summary", "结果摘要") },
      { id: "artifacts", component: "artifact-list", source: "/run/artifacts", title: text("Outputs", "产出文件"), preview_limit: 128 },
    ],
    handoffs: [{ id: "continue", label: text("Continue with Agent", "交给 Agent 继续分析"), summary_source: "/run/result", artifact_sources: ["/run/artifacts"] }],
  },
} as unknown as PublishedToolProduct;

const completedRun = {
  run_id: "run-coral", product_slug: "coral", release_id: "release-coral", action_id: "coral-one-shot", user_id: "alice",
  status: "completed", progress: 100, result: { candidate_count: 1 },
  artifacts: [
    { id: "cif-1", name: "candidate.cif", kind: "chemical/x-cif", available: true, size: 91_170 },
    { id: "cif-2", name: "baseline.cif", kind: "chemical/x-cif", available: true, size: 90_884 },
    { id: "csv-1", name: "pocket_candidates.csv", kind: "text/csv", available: true, size: 667 },
  ],
  usage: { wall_ms: 126_646, gpu_device_ms: 223_772, source: "service_reported" },
  created_at: "2026-10-06T00:00:00Z", updated_at: "2026-10-06T00:02:00Z",
} as ToolRunSnapshot;

function apiFixture(overrides: Partial<ResearchApi> = {}): ResearchApi {
  return {
    getToolProduct: vi.fn(async () => coralProduct),
    getToolProductRuns: vi.fn(async () => [completedRun]),
    startToolProductRun: vi.fn(async () => completedRun),
    getToolProductRun: vi.fn(async () => completedRun),
    getToolProductRunEvents: vi.fn(async () => [
      { event_id: "event-1", run_id: "run-coral", sequence: 1, type: "stage.started", data: { job_id: "job-1", label: "AF3 inference" }, created_at: "2026-10-06T00:00:01Z" },
      { event_id: "event-2", run_id: "run-coral", sequence: 2, type: "stage.progress", data: { job_id: "job-1", progress: 42 }, created_at: "2026-10-06T00:00:02Z" },
    ]),
    cancelToolProductRun: vi.fn(async () => ({ ...completedRun, status: "cancelling" })),
    handoffToolProductRun: vi.fn(async () => ({ run_id: "run-coral", handoff_id: "continue", summary: "One reviewed candidate", artifacts: completedRun.artifacts })),
    createSession: vi.fn(async () => ({ id: "session-coral", project_id: null, title: "CORAL" })),
    sendMessage: vi.fn(async () => ({ run_id: "agent-run" })),
    downloadArtifact: vi.fn(async () => new Blob(["artifact"])),
    ...overrides,
  } as unknown as ResearchApi;
}

function renderPage(api: ResearchApi, initial = "/tools/coral") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><LanguageProvider><MemoryRouter initialEntries={[initial]}>
    <Routes><Route path="/tools/:slug" element={<ToolProductPage api={api} slug="coral" userId="alice" theme="light" onBack={vi.fn()} />} />
      <Route path="/session/:id" element={<div>Agent conversation</div>} /></Routes>
  </MemoryRouter></LanguageProvider></QueryClientProvider>);
}

afterEach(() => { cleanup(); localStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("derives CORAL modes, limits and selected action entirely from the release", async () => {
  localStorage.setItem("research_language", "en");
  const api = apiFixture();
  renderPage(api);
  const actor = userEvent.setup();
  const page = await screen.findByRole("article", { name: "CORAL Scientific Workflows" });
  expect(within(page).getByRole("radio", { name: "One-shot" })).toHaveAttribute("aria-checked", "true");
  expect(within(page).getByRole("spinbutton", { name: "Candidate count" })).toHaveAttribute("max", "10");
  expect(within(page).getByRole("spinbutton", { name: "RNA length" })).toHaveAttribute("max", "500");
  await actor.type(within(page).getByRole("textbox", { name: "PDB ID" }), "1KJS");
  await actor.type(within(page).getByRole("textbox", { name: "Chain" }), "A");
  await actor.click(within(page).getByRole("button", { name: "Run CORAL" }));
  expect(api.startToolProductRun).toHaveBeenCalledWith("coral", "coral-one-shot", {
    pdb_id: "1KJS", chain: "A", num_samples: 1, length: 50,
  }, expect.any(String));
});

it("renders true progress and keeps cancellation on the owned run", async () => {
  localStorage.setItem("research_language", "en");
  const running = { ...completedRun, status: "running" as const, progress: 42, result: null, artifacts: [], usage: null };
  const api = apiFixture({ getToolProductRun: vi.fn(async () => running), cancelToolProductRun: vi.fn(async () => ({ ...running, status: "cancelling" as const })) });
  renderPage(api, "/tools/coral?run=run-coral");
  const actor = userEvent.setup();
  expect(await screen.findByText("AF3 inference")).toBeInTheDocument();
  expect(screen.getByText("Running · 42%")).toBeInTheDocument();
  await actor.click(screen.getByRole("button", { name: "Stop run" }));
  expect(api.cancelToolProductRun).toHaveBeenCalledWith("run-coral");
  expect(screen.getByRole("button", { name: "Stopping…" })).toBeDisabled();
});

it("keeps every owned output downloadable and hands off only immutable run context", async () => {
  localStorage.setItem("research_language", "en");
  const api = apiFixture();
  renderPage(api, "/tools/coral?run=run-coral");
  const actor = userEvent.setup();
  for (const artifact of completedRun.artifacts) {
    expect(await screen.findByText(artifact.name)).toBeInTheDocument();
  }
  await actor.click(screen.getByRole("button", { name: "Download candidate.cif" }));
  expect(api.downloadArtifact).toHaveBeenCalledWith("cif-1");
  await actor.click(screen.getByRole("button", { name: "Continue with Agent" }));
  expect(await screen.findByText("Agent conversation")).toBeInTheDocument();
  expect(api.handoffToolProductRun).toHaveBeenCalledWith("run-coral", "continue");
  const message = vi.mocked(api.sendMessage).mock.calls[0][1];
  expect(message.attachments).toEqual(completedRun.artifacts.map(({ id, name }) => ({ id, name })));
  expect(message.content).toContain("run-coral");
  expect(message.content).toContain("One reviewed candidate");
});

it("requests history for the exact published product", async () => {
  localStorage.setItem("research_language", "en");
  const api = apiFixture();
  renderPage(api, "/tools/coral?tab=history");
  expect(await screen.findByRole("button", { name: /run-coral/ })).toBeInTheDocument();
  expect(api.getToolProductRuns).toHaveBeenCalledWith("coral");
  await waitFor(() => expect(api.getToolProduct).toHaveBeenCalledWith("coral"));
});
