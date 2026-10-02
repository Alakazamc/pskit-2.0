import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { emptyRun, projectEvents } from "./events";
import { AgentPanel } from "./AgentPanel";
import { LanguageProvider } from "../../i18n/LanguageProvider";

it("shows the server plan instead of demo steps", async () => {
  const run = projectEvents(emptyRun, [{ id: "1", run_id: "run-plan", type: "plan.created", data: {
    steps: [{ id: "inspect", title: "Inspect sequences", status: "completed" },
      { id: "report", title: "Write report", status: "in_progress" }],
  } }]);
  render(<LanguageProvider><AgentPanel run={run} savedArtifacts={[]} /></LanguageProvider>);
  await userEvent.click(screen.getByRole("tab", { name: "计划" }));
  expect(screen.getByText("Inspect sequences")).toBeInTheDocument();
  expect(screen.getByText("Write report")).toBeInTheDocument();
});

it("explains an AF3 queue timeout in the selected language", () => {
  const run = projectEvents(emptyRun, [{ id: "1", run_id: "run-timeout", type: "run.failed", data: {
    code: "AF3_COMPUTE_UNAVAILABLE", message: "AlphaFold 3 计算节点未在等待期限内领取任务",
  } }]);
  render(<LanguageProvider><AgentPanel run={run} savedArtifacts={[]} /></LanguageProvider>);
  expect(screen.getByText("AF3 计算节点未及时领取任务，GPU 预留已释放。")).toBeInTheDocument();
});
it("explains that a claimed AF3 timeout keeps GPU minutes pending reconciliation", () => {
  const run = projectEvents(emptyRun, [{ id: "1", run_id: "run-timeout", type: "run.failed", data: {
    code: "AF3_EXECUTION_TIMEOUT", message: "AlphaFold 3 任务超过最长执行时间",
  } }]);
  render(<LanguageProvider><AgentPanel run={run} savedArtifacts={[]} /></LanguageProvider>);
  expect(screen.getByText(/已领取任务的 GPU 预留保留待实际用量对账/)).toBeInTheDocument();
});
it("explains claimed AF3 timeout accounting in English", () => {
  window.localStorage.setItem("research_language", "en");
  const run = projectEvents(emptyRun, [{ id: "1", run_id: "run-timeout", type: "run.failed", data: {
    code: "AF3_EXECUTION_TIMEOUT", message: "AlphaFold 3 任务超过最长执行时间",
  } }]);
  render(<LanguageProvider><AgentPanel run={run} savedArtifacts={[]} /></LanguageProvider>);
  expect(screen.getByText(/GPU reservation remains pending usage reconciliation/)).toBeInTheDocument();
});
afterEach(() => { cleanup(); window.localStorage.removeItem("research_language"); });

it("shows reported activity and does not invent research steps or compute nodes", () => {
  const run = projectEvents(emptyRun, [{
    id: "1", run_id: "run-7", type: "task.updated",
    data: { job_id: "job-7", label: "Protein folding", status: "running", progress: 42 },
  }]);
  render(<AgentPanel run={run} savedArtifacts={[]} />);

  expect(screen.getByText("Protein folding")).toBeInTheDocument();
  expect(screen.queryByText("理解研究问题")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "计算" }));
  expect(screen.queryByText("模拟计算节点")).not.toBeInTheDocument();
  expect(screen.queryByText("MCP 工具演示")).not.toBeInTheDocument();
});

it("renders Agent activity and compute labels in English", async () => {
  window.localStorage.setItem("research_language", "en");
  render(<LanguageProvider><AgentPanel run={emptyRun} savedArtifacts={[]} /></LanguageProvider>);
  expect(screen.getByRole("tab", { name: "Activity" })).toBeInTheDocument();
  await userEvent.setup().click(screen.getByRole("tab", { name: "Compute" }));
  expect(screen.getByText("Compute usage")).toBeInTheDocument();
});

it("shows bounded model retry progress in the activity panel", () => {
  const run = projectEvents(emptyRun, [{
    id: "1", run_id: "run-8", type: "run.retrying",
    data: { attempt: 1, max_attempts: 3, delay_ms: 2000 },
  }]);
  render(<LanguageProvider><AgentPanel run={run} savedArtifacts={[]} /></LanguageProvider>);
  expect(screen.getByText("模型请求重试 1/3")).toBeInTheDocument();
});

it("explains exhausted model gateway quota in the selected language", () => {
  window.localStorage.setItem("research_language", "en");
  const run = projectEvents(emptyRun, [{
    id: "1", run_id: "run-quota", type: "run.failed",
    data: { code: "MODEL_GATEWAY_QUOTA_EXHAUSTED", message: "Pi Agent 执行失败" },
  }]);
  render(<LanguageProvider><AgentPanel run={run} savedArtifacts={[]} /></LanguageProvider>);
  expect(screen.getByText("The model gateway quota is exhausted.")).toBeInTheDocument();
});

it("explains a Token quota block during background resume in English", () => {
  window.localStorage.setItem("research_language", "en");
  const run = projectEvents(emptyRun, [{
    id: "1", run_id: "run-resume", type: "run.failed",
    data: { code: "TOKEN_QUOTA_EXCEEDED", message: "本月 Token 额度不足，无法继续分析" },
  }]);
  render(<LanguageProvider><AgentPanel run={run} savedArtifacts={[]} /></LanguageProvider>);
  expect(screen.getByText("The monthly Token quota is too low to resume this analysis.")).toBeInTheDocument();
});

it("asks for an explicit decision on a high-cost AF3 request", async () => {
  const onApproval = vi.fn();
  const run = projectEvents(emptyRun, [{
    id: "1", run_id: "run-9", type: "approval.required",
    data: { approval_id: "approval-1", capability: "submit_af3", estimated_gpu_minutes: 40 },
  }]);
  render(<LanguageProvider><AgentPanel run={run} savedArtifacts={[]} onApproval={onApproval} /></LanguageProvider>);
  const user = userEvent.setup();
  expect(screen.getByText("AlphaFold 3 需要确认 · 预计 40 GPU 分钟")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "批准执行" }));
  expect(onApproval).toHaveBeenCalledWith("approval-1", "approved");
  await user.click(screen.getByRole("button", { name: "拒绝" }));
  expect(onApproval).toHaveBeenCalledWith("approval-1", "rejected");
});
