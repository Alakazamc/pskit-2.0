import { beforeEach, describe, expect, it, vi } from "vitest";

import { createAgentConversation } from "./agentConversation";
import { ApiError, activeAgentTurn, api, recoverAgentTurn, streamAgentMessage, type AgentMessage, type AgentSession, type AgentStreamResult, type StreamEvent, type Task } from "./api";

vi.mock("./api", async (original) => ({
  ...await original<typeof import("./api")>(),
  api: { sessions: vi.fn(), sessionHistory: vi.fn(), tasks: vi.fn(), createSession: vi.fn() },
  activeAgentTurn: vi.fn(), recoverAgentTurn: vi.fn(), streamAgentMessage: vi.fn(),
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
const session = (id: string): AgentSession => ({ id, title: id, created_at: "2026-01-01", updated_at: "2026-01-01" });
const message = (id: string, metadata: Record<string, unknown> = {}): AgentMessage => ({
  id, role: "assistant", content: id, metadata, created_at: "2026-01-01",
});
const task = (id: string, sessionId: string): Task => ({
  id, session_id: sessionId, tool_call_id: null, task_type: "predict_interaction",
  status: "succeeded", progress: 1, error_type: null, error_message: null,
  input: null, output: null, retry_of_task_id: null, retry_task_id: null,
  artifacts: [{ id: `${id}-artifact`, kind: "result", filename: `${id}.pdb`, mime_type: null, size_bytes: 1, download_url: `/api/files/${id}-artifact/download` }],
  created_at: "2026-01-01", updated_at: "2026-01-01", started_at: null, finished_at: null,
});
const success: AgentStreamResult = { status: "succeeded" };

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.sessions).mockResolvedValue([session("A"), session("B")]);
  vi.mocked(api.tasks).mockResolvedValue([]);
  vi.mocked(api.sessionHistory).mockResolvedValue([]);
  vi.mocked(activeAgentTurn).mockResolvedValue(null);
  vi.mocked(streamAgentMessage).mockResolvedValue(success);
  vi.mocked(recoverAgentTurn).mockResolvedValue(success);
});

describe("Agent conversation lifecycle", () => {
  it("offers an editable follow-up draft only for a new terminal task", async () => {
    vi.mocked(api.sessionHistory).mockResolvedValue([{
      ...message("user request"), role: "user", created_at: "2026-01-01T12:00:00Z",
    }]);
    vi.mocked(api.tasks).mockResolvedValue([
      { ...task("old", "A"), finished_at: "2026-01-01T11:59:00Z" },
      { ...task("running", "A"), status: "running", finished_at: null },
    ]);
    const chat = createAgentConversation();
    await chat.selectSession("A");
    expect(chat.followUpTask.value).toBe(null);

    vi.mocked(api.tasks).mockResolvedValue([{ ...task("completed", "A"), finished_at: "2026-01-01T12:01:00Z" }]);
    await chat.loadTasks();
    expect(chat.followUpTask.value?.id).toBe("completed");
    chat.draftTaskFollowUp();
    expect(chat.input.value).toContain("解读本会话已完成任务 completed");
    expect(streamAgentMessage).not.toHaveBeenCalled();

    vi.mocked(api.tasks).mockResolvedValue([{ ...task("failed", "A"), status: "failed", finished_at: "2026-01-01T12:02:00Z" }]);
    await chat.loadTasks();
    chat.input.value = "";
    chat.draftTaskFollowUp();
    expect(chat.input.value).toContain("分析失败原因");
    expect(streamAgentMessage).not.toHaveBeenCalled();
  });

  it("requests tasks for the current session and ignores a stale prior-session page", async () => {
    const oldTasks = deferred<Task[]>();
    vi.mocked(api.tasks).mockImplementation((query) => query?.sessionId === "A"
      ? oldTasks.promise : Promise.resolve([task("B-result", "B")]));
    const chat = createAgentConversation();
    const openingA = chat.selectSession("A");
    await chat.selectSession("B");
    oldTasks.resolve([task("A-result", "A")]);
    await openingA;
    expect(api.tasks).toHaveBeenCalledWith({ sessionId: "A" });
    expect(api.tasks).toHaveBeenCalledWith({ sessionId: "B" });
    expect(chat.artifacts.value.map((artifact) => artifact.filename)).toEqual(["B-result.pdb"]);
  });

  it("refreshes history when a turn finishes between history and active-turn requests", async () => {
    vi.mocked(api.sessionHistory)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([message("completed answer")]);
    const chat = createAgentConversation();
    await chat.selectSession("A");
    await vi.waitFor(() => expect(chat.messages.value.map((item) => item.content)).toEqual(["completed answer"]));
    expect(api.sessionHistory).toHaveBeenCalledTimes(2);
    expect(activeAgentTurn).toHaveBeenCalledTimes(1);
  });

  it("discards stale history when users switch sessions quickly", async () => {
    const historyA = deferred<AgentMessage[]>();
    vi.mocked(api.sessionHistory).mockImplementation((id) => id === "A" ? historyA.promise : Promise.resolve([message("B answer")]));
    const chat = createAgentConversation();
    const openingA = chat.selectSession("A");
    await chat.selectSession("B");
    historyA.resolve([message("A answer")]);
    await openingA;
    expect(chat.messages.value.map((item) => item.content)).toEqual(["B answer"]);
    expect(chat.activeSessionId.value).toBe("B");
  });

  it("aborts the old subscription and ignores its late events after switching", async () => {
    const streamA = deferred<AgentStreamResult>();
    let oldEvent!: (event: StreamEvent) => void;
    let oldSignal: AbortSignal | undefined;
    vi.mocked(streamAgentMessage).mockImplementation((_id, _content, onEvent, _turnId, options) => {
      oldEvent = onEvent;
      oldSignal = options?.signal;
      return streamA.promise;
    });
    const chat = createAgentConversation();
    await chat.boot();
    chat.input.value = "A request";
    const sending = chat.sendMessage();
    await chat.selectSession("B");
    oldEvent({ type: "message_delta", delta: "A secret answer" });
    streamA.resolve(success);
    await sending;
    expect(oldSignal?.aborted).toBe(true);
    expect(chat.streamingAnswer.value).toBe("");
    expect(chat.messages.value).toEqual([]);
    expect(chat.sending.value).toBe(false);
  });

  it("restores unsent input when a dropped submission cannot be found", async () => {
    vi.mocked(streamAgentMessage).mockRejectedValue(new TypeError("Failed to fetch"));
    vi.mocked(recoverAgentTurn).mockRejectedValue(new ApiError(404, "Agent turn not found"));
    const chat = createAgentConversation();
    await chat.boot();
    chat.input.value = "important request";
    await chat.sendMessage();
    expect(chat.input.value).toBe("important request");
    expect(chat.messages.value).toEqual([]);
    expect(chat.error.value).toContain("输入已保留");
    expect(chat.sending.value).toBe(false);
  });

  it("recovers the exact submitted turn rather than losing already completed turns", async () => {
    vi.mocked(streamAgentMessage).mockRejectedValue(new ApiError(0, "disconnected"));
    const chat = createAgentConversation();
    await chat.boot();
    chat.input.value = "run analysis";
    await chat.sendMessage();
    const submittedId = vi.mocked(streamAgentMessage).mock.calls[0]?.[3];
    expect(recoverAgentTurn).toHaveBeenCalledWith(submittedId, expect.any(Function), expect.any(AbortSignal));
    expect(activeAgentTurn).toHaveBeenCalledTimes(1); // Only the initial session check.
  });

  it("retains failed execution feedback after history refresh", async () => {
    vi.mocked(streamAgentMessage).mockResolvedValue({ status: "failed", error: "模型配置缺失" });
    const chat = createAgentConversation();
    await chat.boot();
    chat.input.value = "run";
    await chat.sendMessage();
    expect(chat.error.value).toBe("模型配置缺失");
    expect(chat.sending.value).toBe(false);
  });

  it("keeps a conflicting new message as a draft while recovering the prior turn", async () => {
    vi.mocked(streamAgentMessage).mockRejectedValue(new ApiError(409, "already running"));
    const chat = createAgentConversation();
    await chat.boot();
    vi.mocked(activeAgentTurn).mockResolvedValue({
      turn_id: "prior-server-turn", client_turn_id: "prior-client-turn", status: "running",
      user_content: "older request", error_code: null, created_at: "2026-01-01", updated_at: "2026-01-01",
    });
    chat.input.value = "new request";
    await chat.sendMessage();
    expect(chat.input.value).toBe("new request");
    expect(chat.notice.value).toContain("本次输入已保留");
    expect(recoverAgentTurn).toHaveBeenCalledWith("prior-server-turn", expect.any(Function), expect.any(AbortSignal));
    expect(chat.sending.value).toBe(false);
  });

  it("releases sending state and reports a failed active-turn lookup", async () => {
    vi.mocked(activeAgentTurn).mockRejectedValue(new ApiError(503, "service unavailable"));
    const chat = createAgentConversation();
    await chat.boot();
    expect(chat.sending.value).toBe(false);
    expect(chat.error.value).toBe("service unavailable");
  });

  it("restores result files and pending approval from history, and sends the approval ID", async () => {
    const approval = { approval_id: "a".repeat(64), tool_name: "run_alphafold3" };
    vi.mocked(api.sessionHistory).mockResolvedValue([message("requires confirmation", {
      approval, sources: [{ source: "source.md" }], rag_backend: "qdrant",
      artifacts: [{ artifact_id: "file", filename: "result.pdb", download_url: "/api/files/file/download" }],
    })]);
    const chat = createAgentConversation();
    await chat.boot();
    expect(chat.artifacts.value[0]?.filename).toBe("result.pdb");
    expect(chat.sources.value[0]?.source).toBe("source.md");
    expect(chat.pendingApproval.value?.approval_id).toBe(approval.approval_id);
    await chat.approvePending();
    expect(streamAgentMessage).toHaveBeenCalledWith("A", "确认执行 run_alphafold3", expect.any(Function), expect.any(String), expect.objectContaining({ approvalId: approval.approval_id }));
  });

  it("does not redisplay an approval that was consumed by a later user message", async () => {
    const approval = { approval_id: "a".repeat(64), tool_name: "run_alphafold3" };
    vi.mocked(api.sessionHistory).mockResolvedValue([
      message("waiting", { approval }),
      { ...message("confirmed", { approval_id: approval.approval_id }), role: "user" },
      message("submitted"),
    ]);
    const chat = createAgentConversation();
    await chat.boot();
    expect(chat.pendingApproval.value).toBe(null);
  });
});
