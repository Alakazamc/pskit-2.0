import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { emptyRun, projectEvents } from "./events";
import { Conversation } from "./Conversation";
import { LanguageProvider } from "../../i18n/LanguageProvider";
import type { Message } from "../../api/types";

const longMessage: Message = {
  id: "user-1", session_id: "session-1", role: "user", created_at: "2026-10-01T00:00:00Z",
  parts: [{ type: "text", text: "A long research prompt. ".repeat(60) }, { type: "file", id: "file-1", name: "paper.pdf" }],
};

const originalScrollHeight = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollHeight");
const originalClientHeight = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "clientHeight");

afterEach(() => {
  cleanup();
  for (const [name, descriptor] of [["scrollHeight", originalScrollHeight], ["clientHeight", originalClientHeight]] as const) {
    if (descriptor) Object.defineProperty(HTMLElement.prototype, name, descriptor);
    else Reflect.deleteProperty(HTMLElement.prototype, name);
  }
  vi.restoreAllMocks();
});

it("collapses a long user message while keeping its file visible and the reading position stable", () => {
  Object.defineProperty(HTMLElement.prototype, "scrollHeight", { configurable: true, get() { return this.classList?.contains("user-message-text") ? 600 : 0; } });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, get() { return this.classList?.contains("user-message-text") ? 120 : 0; } });

  const { container } = render(<LanguageProvider><Conversation messages={[longMessage]} run={emptyRun} /></LanguageProvider>);
  const scroller = container.querySelector<HTMLElement>(".conversation-scroll")!;
  const text = container.querySelector<HTMLElement>(".user-message-text")!;
  expect(text).toHaveClass("is-collapsed");
  expect(screen.getByText("paper.pdf")).toBeVisible();

  const toggle = screen.getByRole("button", { name: "展开" });
  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute("aria-expanded", "true");
  expect(text).not.toHaveClass("is-collapsed");

  scroller.scrollTop = 600;
  vi.spyOn(toggle, "getBoundingClientRect").mockImplementation(() => ({ top: toggle.getAttribute("aria-expanded") === "true" ? 420 : 80 }) as DOMRect);
  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  expect(scroller.scrollTop).toBe(260);
});

it("leaves a short user message fully visible without an expand action", () => {
  Object.defineProperty(HTMLElement.prototype, "scrollHeight", { configurable: true, get() { return 80; } });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, get() { return 80; } });
  const message = { ...longMessage, parts: [{ type: "text" as const, text: "Short question" }] };
  render(<LanguageProvider><Conversation messages={[message]} run={emptyRun} /></LanguageProvider>);
  expect(screen.queryByRole("button", { name: "展开" })).not.toBeInTheDocument();
  expect(screen.getByText("Short question")).toBeVisible();
});

it("uses the task label received from the event stream", () => {
  const run = projectEvents(emptyRun, [{
    id: "1", run_id: "run-1", type: "task.updated",
    data: { job_id: "job-1", label: "Sequence alignment", status: "running", progress: 42 },
  }]);

  render(<Conversation messages={[]} run={run} />);

  expect(screen.getByText(/Sequence alignment/)).toBeInTheDocument();
  expect(screen.queryByText(/AF3 正在计算/)).not.toBeInTheDocument();
});

it("shows an active tool from the event stream", () => {
  const run = projectEvents(emptyRun, [{
    id: "1", run_id: "run-1", type: "tool.started",
    data: { tool_call_id: "call-1", tool: "search_pdb" },
  }]);
  render(<Conversation messages={[]} run={run} />);
  expect(screen.getByText(/search_pdb/)).toBeInTheDocument();
});

it("shows a server plan in the active Mono conversation", () => {
  const run = projectEvents(emptyRun, [{ id: "1", run_id: "run-plan", type: "plan.created", data: {
    steps: [{ id: "inspect", title: "Inspect sequences", status: "completed" },
      { id: "report", title: "Write report", status: "in_progress" }],
  } }]);
  render(<LanguageProvider><Conversation messages={[]} run={run} /></LanguageProvider>);
  expect(screen.getByRole("region", { name: "计划" })).toBeInTheDocument();
  expect(screen.getByText("Inspect sequences")).toBeInTheDocument();
  expect(screen.getByText("Write report")).toBeInTheDocument();
});

it("shows a specific quota failure instead of a generic Pi error", () => {
  const run = projectEvents(emptyRun, [{
    id: "1", run_id: "run-quota", type: "run.failed",
    data: { code: "MODEL_GATEWAY_QUOTA_EXHAUSTED", message: "Pi Agent 执行失败" },
  }]);
  render(<LanguageProvider><Conversation messages={[]} run={run} /></LanguageProvider>);
  expect(screen.getByText("模型网关额度已耗尽。")).toBeInTheDocument();
});
