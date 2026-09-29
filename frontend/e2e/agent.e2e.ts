import { expect, test, type Page, type Route } from "@playwright/test";

// Browser acceptance of the UI/API contract; intentionally no scientific service
// credentials or external inference are needed for these deterministic scenarios.
type Message = { id: string; role: string; content: string; created_at: string; metadata: Record<string, unknown> };
type MockState = {
  history: Record<string, Message[]>;
  onMessage?: (route: Route, sessionId: string) => Promise<void>;
  onRecovery?: (route: Route, turnId: string) => Promise<void>;
  onTasks?: (route: Route) => Promise<void>;
};
const timestamp = "2026-01-01T00:00:00Z";
function message(id: string, role: string, content: string, metadata: Record<string, unknown> = {}): Message {
  return { id, role, content, metadata, created_at: timestamp };
}
const sse = (...events: Record<string, unknown>[]) => events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join("");
const sendStream = (route: Route, ...events: Record<string, unknown>[]) => route.fulfill({ contentType: "text/event-stream", body: sse(...events) });

async function mockApi(page: Page, state: MockState) {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/me") return route.fulfill({ json: { id: "user", username: "test-user", role: "user" } });
    if (path === "/api/agent/sessions") return route.fulfill({ json: ["A", "B"].map((id) => ({ id, title: `分析 ${id}`, created_at: timestamp, updated_at: timestamp })) });
    if (path === "/api/tasks") return state.onTasks ? state.onTasks(route) : route.fulfill({ json: [] });
    if (path.endsWith("/turns/active")) return route.fulfill({ status: 204 });
    const post = path.match(/^\/api\/agent\/sessions\/([^/]+)\/message$/);
    if (post && state.onMessage) return state.onMessage(route, post[1]!);
    const recovery = path.match(/^\/api\/agent\/turns\/([^/]+)\/events$/);
    if (recovery && state.onRecovery) return state.onRecovery(route, recovery[1]!);
    const history = path.match(/^\/api\/agent\/sessions\/([^/]+)$/);
    if (history) return route.fulfill({ json: state.history[history[1]!] || [] });
    return route.fulfill({ status: 404, json: { detail: "Not found" } });
  });
}

test("the seventh and 101st same-session result files remain reachable without polling old pages", async ({ page }) => {
  await page.clock.install();
  const offsets: number[] = [];
  const tasks = Array.from({ length: 101 }, (_, index) => {
    const number = index + 1;
    const when = new Date(Date.UTC(2026, 0, 1) + (101 - index) * 1000).toISOString();
    return {
      id: `task-${number}`, session_id: "A", tool_call_id: null, task_type: `analysis-${number}`,
      status: "succeeded", progress: 1, error_type: null, error_message: null,
      input: null, output: null, retry_of_task_id: null, retry_task_id: null,
      artifacts: [{ id: `artifact-${number}`, kind: "result", filename: `file-${number}.pdb`, mime_type: null, size_bytes: 1, download_url: `/api/files/artifact-${number}/download` }],
      created_at: when, updated_at: when, started_at: when, finished_at: when,
    };
  });
  await mockApi(page, {
    history: {},
    async onTasks(route) {
      const url = new URL(route.request().url());
      expect(url.searchParams.get("session_id")).toBe("A");
      const offset = Number(url.searchParams.get("offset") || 0);
      const limit = Number(url.searchParams.get("limit") || 100);
      offsets.push(offset);
      await route.fulfill({ json: tasks.slice(offset, offset + limit) });
    },
  });
  await page.goto("/agent");
  await expect(page.locator(".agent-rail .rail-card").nth(1).locator("a.artifact-link").first()).toContainText("file-1.pdb");
  await expect(page.getByRole("link", { name: "file-7.pdb" })).toBeHidden();
  await page.getByText("查看另外 94 个结果文件", { exact: true }).click();
  await expect(page.getByRole("link", { name: "file-7.pdb" })).toBeVisible();
  await expect(page.getByRole("link", { name: "file-101.pdb" })).toHaveCount(0);

  await page.getByRole("button", { name: "加载更早任务与文件" }).click();
  await expect(page.getByRole("link", { name: "file-101.pdb" })).toHaveAttribute("href", "/api/files/artifact-101/download");
  await expect(page.getByRole("link", { name: "file-101.pdb" })).toBeVisible();
  await page.getByText("查看另外 97 个任务", { exact: true }).click();
  await expect(page.getByText("analysis-101", { exact: true })).toBeVisible();
  expect(offsets.filter((offset) => offset === 100)).toHaveLength(1);

  await page.clock.fastForward(3000);
  await expect.poll(() => offsets.filter((offset) => offset === 0).length).toBeGreaterThan(1);
  expect(offsets.filter((offset) => offset === 100)).toHaveLength(1);
});

test("HTTP without randomUUID can send, and execution errors stay visible", async ({ page }) => {
  await page.addInitScript(() => Object.defineProperty(window.crypto, "randomUUID", { value: undefined }));
  let turnId = "";
  const state: MockState = {
    history: {},
    async onMessage(route) {
      const body = route.request().postDataJSON();
      turnId = body.turn_id;
      state.history.A = [message("user", "user", body.content)];
      await sendStream(route,
        { type: "error", error: { message: "模拟模型执行失败" } },
        { type: "done" },
      );
    },
  };
  await mockApi(page, state);
  await page.goto("/agent");
  await page.getByRole("textbox").fill("下载 7U5E");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.locator(".chat-panel > .error-line")).toHaveText("模拟模型执行失败");
  expect(turnId).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  await page.getByRole("textbox").fill("再试一次");
  await expect(page.getByRole("button", { name: "发送", exact: true })).toBeEnabled();
});

test("AF3 confirmation survives refresh and carries the approval token exactly once", async ({ page }) => {
  const approval = { approval_id: "a".repeat(64), tool_name: "run_alphafold3", message: "即将使用 GPU 运行结构预测。" };
  const requests: Record<string, string>[] = [];
  const state: MockState = {
    history: {},
    async onMessage(route) {
      const body = route.request().postDataJSON();
      requests.push(body);
      if (!body.approval_id) {
        state.history.A = [message("waiting", "assistant", "请确认执行。", { approval })];
        await sendStream(route, { type: "approval_required", ...approval }, { type: "done" });
      } else {
        state.history.A!.push(message("confirmation", "user", body.content, { approval_id: body.approval_id }));
        state.history.A!.push(message("completed", "assistant", "AF3 任务已提交。"));
        await sendStream(route, { type: "approval_consumed", approval_id: body.approval_id }, { type: "done" });
      }
    },
  };
  await mockApi(page, state);
  await page.goto("/agent");
  await page.getByRole("textbox").fill("运行 AlphaFold3");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByRole("button", { name: "确认执行", exact: true })).toBeEnabled();
  await page.reload();
  await page.getByRole("button", { name: "确认执行", exact: true }).click();
  await expect(page.getByText("AF3 任务已提交。", { exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "待确认操作" })).toHaveCount(0);
  expect(requests).toHaveLength(2);
  expect(requests[1]!.approval_id).toBe(approval.approval_id);
});

test("switching sessions during a request cannot leak its reply or keep the new composer locked", async ({ page }) => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  let received = false;
  const state: MockState = {
    history: { B: [message("B-reply", "assistant", "这是 B 的历史记录")] },
    async onMessage(route, sessionId) {
      if (sessionId === "A") {
        received = true;
        await gate;
        await sendStream(route, { type: "message_delta", delta: "仅属于 A 的回复" }, { type: "done" });
      } else {
        state.history.B!.push(message("B-complete", "assistant", "B 请求完成"));
        await sendStream(route, { type: "done" });
      }
    },
  };
  await mockApi(page, state);
  await page.goto("/agent");
  await page.getByRole("textbox").fill("A 正在计算");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect.poll(() => received).toBe(true);
  await page.getByRole("button", { name: /^分析 B/ }).click();
  await expect(page.getByText("这是 B 的历史记录", { exact: true })).toBeVisible();
  release();
  await page.getByRole("textbox").fill("B 的请求");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByText("B 请求完成", { exact: true })).toBeVisible();
  await expect(page.getByText("仅属于 A 的回复", { exact: true })).toHaveCount(0);
});

test("failed network submission restores the draft without a phantom sent message", async ({ page }) => {
  const state: MockState = { history: {}, onMessage: (route) => route.abort("failed") };
  await mockApi(page, state);
  await page.goto("/agent");
  await page.getByRole("textbox").fill("不能丢失的请求");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.locator(".chat-panel > .error-line")).toContainText("输入已保留");
  await expect(page.getByRole("textbox")).toHaveValue("不能丢失的请求");
  await expect(page.locator(".message-card.user")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "发送", exact: true })).toBeEnabled();
});

test("a dropped stream recovers the original turn even after execution has completed", async ({ page }) => {
  let postedTurn = "";
  let recoveredTurn = "";
  const state: MockState = {
    history: {},
    async onMessage(route) {
      postedTurn = route.request().postDataJSON().turn_id;
      await sendStream(route, { type: "heartbeat" }); // EOF without done.
    },
    async onRecovery(route, turnId) {
      recoveredTurn = turnId;
      state.history.A = [message("recovered", "assistant", "原任务已完成并恢复")];
      await sendStream(route, { type: "done", status: "succeeded", recovered: true });
    },
  };
  await mockApi(page, state);
  await page.goto("/agent");
  await page.getByRole("textbox").fill("需要恢复的任务");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByText("原任务已完成并恢复", { exact: true })).toBeVisible();
  expect(recoveredTurn).toBe(postedTurn);
  expect(postedTurn).not.toBe("");
});

test("reopening a completed conversation restores files and RAG sources", async ({ page }) => {
  const state: MockState = {
    history: { A: [message("saved", "assistant", "结果已保存", {
      artifacts: [{ artifact_id: "artifact", filename: "7U5E.pdb", download_url: "/api/files/artifact/download" }],
      sources: [{ source: "research.md", heading: "结合位点说明", score: 0.9 }],
      rag_backend: "qdrant", suggestions: ["查看报告"],
    })] },
  };
  await mockApi(page, state);
  await page.goto("/agent");
  await expect(page.getByRole("link", { name: "7U5E.pdb", exact: true })).toHaveAttribute("href", "/api/files/artifact/download");
  await expect(page.getByText("结合位点说明", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /^分析 B/ }).click();
  await expect(page.getByRole("link", { name: "7U5E.pdb", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: /^分析 A/ }).click();
  await expect(page.getByRole("button", { name: "查看报告", exact: true })).toBeVisible();
});
