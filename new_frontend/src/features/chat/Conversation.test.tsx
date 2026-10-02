import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

it("renders saved assistant Markdown as headings, tables, code, and math", async () => {
  const message: Message = {
    id: "assistant-1", session_id: "session-1", role: "assistant", created_at: "2026-10-01T00:00:00Z",
    parts: [{ type: "text", text: "## 结果\n\n**蛋白质**\n\n| 位点 | 分数 |\n| --- | --- |\n| A1 | 0.9 |\n\n```python\nprint('ok')\n```\n\n$$E=mc^2$$" }],
  };
  render(<LanguageProvider><Conversation messages={[message]} run={emptyRun} /></LanguageProvider>);
  expect(await screen.findByRole("heading", { name: "结果" })).toBeInTheDocument();
  expect(screen.getByText("蛋白质").closest("strong")).toBeInTheDocument();
  expect(screen.getByRole("table")).toHaveTextContent("A1");
  expect(screen.getByText("print('ok')")).toBeInTheDocument();
  await waitFor(() => expect(document.querySelector(".katex")).toBeInTheDocument());
});

it("copies a Markdown table and opens its focused preview", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
  const message: Message = {
    id: "assistant-table", session_id: "session-1", role: "assistant", created_at: "2026-10-01T00:00:00Z",
    parts: [{ type: "text", text: "| 位点 | 分数 |\n| --- | --- |\n| A1 | 0.9 |" }],
  };
  render(<LanguageProvider><Conversation messages={[message]} run={emptyRun} /></LanguageProvider>);
  expect(await screen.findByRole("table")).toHaveTextContent("A1");
  expect(screen.queryByRole("button", { name: "下载表格" })).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "复制表格" }));
  await waitFor(() => expect(writeText).toHaveBeenCalledWith(expect.stringContaining("| A1 | 0.9 |")));

  fireEvent.click(screen.getByRole("button", { name: "展开表格" }));
  const dialog = await screen.findByRole("dialog", { name: "表格预览" });
  expect(within(dialog).getByRole("table")).toHaveTextContent("A1");
  fireEvent.click(within(dialog).getByRole("button", { name: "关闭表格" }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "表格预览" })).not.toBeInTheDocument());
});

it("renders incomplete streamed Markdown and shows a spinner without a progress bar", async () => {
  const first = projectEvents(emptyRun, [
    { id: "delta-1", run_id: "run-stream", type: "message.delta", data: { delta: "**部分" } },
  ]);
  const { rerender } = render(<LanguageProvider><Conversation messages={[]} run={first} /></LanguageProvider>);
  expect((await screen.findByText("部分")).closest("strong")).toBeInTheDocument();
  expect(screen.getByRole("status", { name: "正在生成回复" })).toBeInTheDocument();
  expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();

  const second = projectEvents(first, [
    { id: "delta-2", run_id: "run-stream", type: "message.delta", data: { delta: "内容**" } },
  ]);
  rerender(<LanguageProvider><Conversation messages={[]} run={second} /></LanguageProvider>);
  expect((await screen.findByText("部分内容")).closest("strong")).toBeInTheDocument();
});

it("copies the raw text of each assistant reply from its lower-left action", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
  const message: Message = {
    id: "assistant-copy", session_id: "session-1", role: "assistant", created_at: "2026-10-01T00:00:00Z",
    parts: [{ type: "text", text: "**复制这段**" }],
  };
  render(<LanguageProvider><Conversation messages={[message]} run={emptyRun} /></LanguageProvider>);
  fireEvent.click(screen.getByRole("button", { name: "复制回复" }));
  await waitFor(() => expect(writeText).toHaveBeenCalledWith("**复制这段**"));
  expect(screen.getByRole("button", { name: "已复制" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "已复制" }).closest(".message-actions")).toBeInTheDocument();
});
