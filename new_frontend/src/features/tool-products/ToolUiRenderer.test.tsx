import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ToolRunEvent, ToolRunSnapshot } from "../../api/generated";
import { LanguageProvider } from "../../i18n/LanguageProvider";
import { ToolUiRenderer } from "./ToolUiRenderer";
import type { ToolUiSchema } from "./toolUiSchema";
import { resolvePointer } from "./toolUiSchema";

vi.mock("../mono/MolstarCanvas", () => ({
  default: ({ source }: { source: { type: string; id?: string } }) =>
    <div data-testid="molstar-canvas">{source.type}:{source.id}</div>,
}));

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.restoreAllMocks();
});

const text = (en: string, zh: string) => ({ en, "zh-CN": zh });

it("accepts configured JSON input as an object and blocks invalid JSON instead of submitting stale input", async () => {
  const onAction = vi.fn();
  const schema = schemaFixture();
  schema.state = {};
  schema.sections = [{ id: "af3", title: text("Input", "输入"), fields: [{
    id: "fold", component: "json-input", label: text("Fold input", "折叠输入"),
    input_pointer: "/form/fold_input", required: true, default: { name: "example" },
  }] }];
  schema.actions = [{ id: "submit", label: text("Run AF3", "运行 AF3"), target: { action_id: "af3-predict" } }];
  function JsonHarness() {
    const [form, setForm] = useState<Record<string, unknown>>({});
    return <LanguageProvider><ToolUiRenderer schema={schema} form={form} run={null} events={[]}
      onChange={setForm} onAction={onAction} /></LanguageProvider>;
  }
  render(<JsonHarness />);
  const user = userEvent.setup();
  const input = screen.getByRole("textbox");
  await user.clear(input);
  await user.paste('{"name":"my protein"}');
  await user.click(screen.getByRole("button", { name: /AF3/ }));
  expect(onAction).toHaveBeenCalledWith("af3-predict", { fold_input: { name: "my protein" } });
  onAction.mockClear();
  await user.clear(input);
  await user.paste('{"name":');
  expect(input).toBeInvalid();
  await user.click(screen.getByRole("button", { name: /AF3/ }));
  expect(onAction).not.toHaveBeenCalled();
});

function schemaFixture(): ToolUiSchema {
  return {
    schema_version: "pskit.tool-ui.v1",
    product: {
      slug: "coral",
      title: text("CORAL RNA Design", "CORAL RNA 设计"),
      description: text("Design candidates", "设计候选序列"),
    },
    page: { layout: "split-workspace", input_width: 5, result_width: 7 },
    state: { mode: { initial: "one-shot" } },
    sections: [
      {
        id: "target",
        title: text("Target", "目标"),
        fields: [
          { id: "name", component: "text-input", label: text("Name", "名称"), input_pointer: "/form/name", required: true },
          { id: "count", component: "number-input", label: text("Count", "数量"), input_pointer: "/form/count", minimum: 1, maximum: 10, step: 1, default: 3 },
          { id: "notes", component: "textarea", label: text("Notes", "备注"), input_pointer: "/form/notes" },
          { id: "method", component: "select", label: text("Method", "方法"), input_pointer: "/form/method", default: "fast", options: [
            { value: "fast", label: text("Fast", "快速") }, { value: "deep", label: text("Deep", "深度") },
          ] },
          { id: "mode", component: "segmented-control", label: text("Mode", "模式"), input_pointer: "/form/mode", options: [
            { value: "one-shot", label: text("One shot", "单次") }, { value: "iterative", label: text("Iterative", "迭代") },
          ] },
          { id: "enabled", component: "switch", label: text("Refine", "精修"), input_pointer: "/form/enabled" },
          { id: "confirmed", component: "checkbox", label: text("Confirmed", "已确认"), input_pointer: "/form/confirmed" },
          { id: "protein", component: "protein-input", label: text("Protein", "蛋白质"), input_pointer: "/form/protein", required: true },
          { id: "sequence", component: "sequence-input", label: text("Sequence", "序列"), input_pointer: "/form/sequence" },
          { id: "files", component: "file-upload", label: text("Files", "文件"), input_pointer: "/form/files", accepted_types: ["text/plain"], max_files: 2 },
          { id: "advanced", component: "advanced-section", label: text("Advanced", "高级"), fields: [
            { id: "temperature", component: "number-input", label: text("Temperature", "温度"), input_pointer: "/form/temperature", default: 0.5 },
          ] },
        ],
      },
      {
        id: "iteration",
        title: text("Iteration", "迭代参数"),
        visible_when: { source: "/form/mode", equals: "iterative" },
        fields: [{ id: "rounds", component: "number-input", label: text("Rounds", "轮数"), input_pointer: "/form/rounds", default: 2 }],
      },
    ],
    actions: [{
      id: "run",
      label: text("Run", "运行"),
      target: { by_state: { source: "/form/mode", map: { "one-shot": "coral-one-shot", iterative: "coral-iterative" } } },
    }],
    result_views: [
      { id: "metrics", component: "metric-grid", source: "/run/result/metrics", title: text("Metrics", "指标") },
      { id: "stages", component: "stage-flow", source: "/run", title: text("Progress", "进度") },
      { id: "sequences", component: "sequence-table", source: "/run/result/sequences", title: text("Sequences", "序列结果"), preview_limit: 2 },
      { id: "rows", component: "data-table", source: "/run/result/rows", title: text("Data", "数据") },
      { id: "line", component: "line-chart", source: "/run/result/points", title: text("Line", "折线") },
      { id: "scatter", component: "scatter-plot", source: "/run/result/points", title: text("Scatter", "散点") },
      { id: "heat", component: "heatmap", source: "/run/result/heatmap", title: text("Heatmap", "热图") },
      { id: "structure", component: "structure-viewer", source: "/run/result/structure", title: text("Structure", "结构") },
      { id: "artifacts", component: "artifact-list", source: "/run/artifacts", title: text("Artifacts", "产物") },
      { id: "raw", component: "json-inspector", source: "/run/result/raw", title: text("Details", "详情"), preview_limit: 60 },
    ],
    handoffs: [],
  };
}

const run: ToolRunSnapshot = {
  run_id: "run-1", product_slug: "coral", release_id: "release-1", action_id: "coral-one-shot", user_id: "alice",
  status: "completed", progress: 100,
  result: {
    metrics: { score: 0.91, candidates: 2 },
    sequences: [{ id: "rna-1", sequence: "ACGU" }, { id: "rna-2", sequence: "UUAG" }, { id: "rna-3", sequence: "GCAU" }],
    rows: [{ sample: "A", value: 3 }, { sample: "B", value: 5 }],
    points: [{ x: 1, y: 2 }, { x: 2, y: 4 }], heatmap: [[1, 2], [3, 4]],
    structure: { pdb_id: "1A9N" },
    raw: { html: "<img src=x onerror=alert(1)>", payload: "x".repeat(300) },
  },
  artifacts: [{ id: "artifact-1", name: "candidates.csv", kind: "csv", available: true, size: 123 }],
  usage: null, created_at: "2026-10-06T00:00:00Z", updated_at: "2026-10-06T00:01:00Z",
};

const events: ToolRunEvent[] = [
  { event_id: "e1", run_id: "run-1", sequence: 1, type: "stage.started", data: { job_id: "job-1", label: "Generate" }, created_at: "2026-10-06T00:00:01Z" },
  { event_id: "e2", run_id: "run-1", sequence: 2, type: "stage.progress", data: { job_id: "job-1", progress: 55 }, created_at: "2026-10-06T00:00:02Z" },
  { event_id: "e3", run_id: "run-1", sequence: 3, type: "stage.completed", data: { job_id: "job-1", status: "completed" }, created_at: "2026-10-06T00:00:03Z" },
];

function Harness({ initial = {}, onAction = vi.fn(), currentRun = null, currentEvents = [] }: {
  initial?: Record<string, unknown>;
  onAction?: (actionId: string, form: Record<string, unknown>) => void;
  currentRun?: ToolRunSnapshot | null;
  currentEvents?: ToolRunEvent[];
}) {
  const [form, setForm] = useState(initial);
  return <LanguageProvider><ToolUiRenderer schema={schemaFixture()} form={form} run={currentRun} events={currentEvents} onChange={setForm} onAction={onAction} /></LanguageProvider>;
}

describe("Tool UI input interpreter", () => {
  it("renders allowlisted bilingual inputs, defaults, ranges and conditional sections", async () => {
    localStorage.setItem("research_language", "en");
    render(<Harness />);
    expect(screen.getByRole("heading", { name: "CORAL RNA Design" })).toBeInTheDocument();
    expect(screen.getByRole("spinbutton", { name: "Count" })).toHaveValue(3);
    expect(screen.getByRole("spinbutton", { name: "Count" })).toHaveAttribute("min", "1");
    expect(screen.getByRole("spinbutton", { name: "Count" })).toHaveAttribute("max", "10");
    expect(screen.getByRole("combobox", { name: "Method" })).toHaveValue("fast");
    expect(screen.getByRole("textbox", { name: "Protein" })).toHaveAttribute("required");
    expect(screen.getByRole("textbox", { name: "Sequence" })).toBeInTheDocument();
    expect(screen.getByLabelText("Files")).toHaveAttribute("accept", "text/plain");
    expect(screen.queryByRole("heading", { name: "Iteration" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("radio", { name: "Iterative" }));
    expect(screen.getByRole("heading", { name: "Iteration" })).toBeInTheDocument();
    expect(screen.getByRole("spinbutton", { name: "Rounds" })).toHaveValue(2);
  });

  it("updates typed values and resolves the selected product action with defaults", async () => {
    localStorage.setItem("research_language", "en");
    const onAction = vi.fn();
    render(<Harness onAction={onAction} />);
    const actor = userEvent.setup();
    await actor.type(screen.getByRole("textbox", { name: "Name" }), "Experiment A");
    await actor.type(screen.getByRole("textbox", { name: "Protein" }), "1A9N");
    await actor.click(screen.getByRole("checkbox", { name: "Confirmed" }));
    await actor.click(screen.getByRole("switch", { name: "Refine" }));
    await actor.upload(screen.getByLabelText("Files"), new File(["hello"], "notes.txt", { type: "text/plain" }));
    await actor.click(screen.getByRole("button", { name: "Run" }));
    expect(onAction).toHaveBeenCalledTimes(1);
    expect(onAction.mock.calls[0][0]).toBe("coral-one-shot");
    expect(onAction.mock.calls[0][1]).toMatchObject({ name: "Experiment A", protein: "1A9N", count: 3, method: "fast", confirmed: true, enabled: true });
    expect((onAction.mock.calls[0][1].files as File[])[0].name).toBe("notes.txt");
  });

  it("uses native required and range validation before dispatching an action", async () => {
    localStorage.setItem("research_language", "en");
    const onAction = vi.fn();
    render(<Harness onAction={onAction} />);
    await userEvent.click(screen.getByRole("button", { name: "Run" }));
    expect(onAction).not.toHaveBeenCalled();
    expect(screen.getByRole("textbox", { name: "Name" })).toBeInvalid();
  });
});

describe("Tool UI result interpreter", () => {
  it("renders scientific result components from persisted run data and events", async () => {
    localStorage.setItem("research_language", "en");
    render(<Harness currentRun={run} currentEvents={events} />);
    expect(screen.getByText("0.91")).toBeInTheDocument();
    expect(screen.getByText("Generate")).toBeInTheDocument();
    expect(screen.getByText("55%")).toBeInTheDocument();
    expect(screen.getByText("ACGU")).toBeInTheDocument();
    expect(screen.queryByText("GCAU")).not.toBeInTheDocument();
    expect(within(screen.getByRole("table", { name: "Data" })).getByText("sample")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Line" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Scatter" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Heatmap" })).toBeInTheDocument();
    expect(await screen.findByTestId("molstar-canvas")).toHaveTextContent("pdb:1A9N");
    expect(screen.getByText("candidates.csv")).toBeInTheDocument();
    expect(screen.getByText(/truncated/i)).toBeInTheDocument();
  });

  it("shows honest loading, failed and empty states", () => {
    localStorage.setItem("research_language", "en");
    const { rerender } = render(<Harness currentRun={{ ...run, status: "running", progress: 12, result: null, artifacts: [] }} />);
    expect(screen.getByRole("status")).toHaveTextContent("Running");
    rerender(<Harness currentRun={{ ...run, status: "failed", progress: 12, result: null, artifacts: [] }} />);
    expect(screen.getByRole("alert")).toHaveTextContent("failed");
    rerender(<Harness currentRun={{ ...run, result: {}, artifacts: [] }} />);
    expect(screen.getAllByText("No data").length).toBeGreaterThan(0);
  });
});

describe("Tool UI safety and accessibility", () => {
  it("resolves RFC 6901 pointers but rejects malformed and prototype paths", () => {
    expect(resolvePointer({ "a/b": { "~key": 7 } }, "/a~1b/~0key")).toBe(7);
    expect(resolvePointer({ safe: 1 }, "safe")).toBeUndefined();
    expect(resolvePointer({}, "/__proto__/polluted")).toBeUndefined();
    expect(resolvePointer({}, "/constructor/prototype")).toBeUndefined();
  });

  it("fails closed for an unsupported component and renders remote markup as text without network execution", () => {
    localStorage.setItem("research_language", "en");
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    const invalid = schemaFixture();
    invalid.sections![0].fields.push({
      id: "unsafe", component: "iframe", label: text("Unsafe", "不安全"), input_pointer: "/form/unsafe",
    } as never);
    render(<LanguageProvider><ToolUiRenderer schema={invalid} form={{}} run={run} events={events} onChange={vi.fn()} onAction={vi.fn()} /></LanguageProvider>);
    expect(screen.getByRole("alert")).toHaveTextContent("Unsupported component");
    expect(document.querySelector(".tool-ui-json")).toHaveTextContent("<img src=x onerror=alert(1)>");
    expect(document.querySelector("img[src='x']")).toBeNull();
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("supports Chinese labels and keyboard submission", async () => {
    const onAction = vi.fn();
    render(<Harness initial={{ name: "实验", protein: "1A9N" }} onAction={onAction} />);
    expect(screen.getByRole("heading", { name: "CORAL RNA 设计" })).toBeInTheDocument();
    screen.getByRole("button", { name: "运行" }).focus();
    await userEvent.keyboard("{Enter}");
    expect(onAction).toHaveBeenCalledWith("coral-one-shot", expect.objectContaining({ protein: "1A9N" }));
  });
});
