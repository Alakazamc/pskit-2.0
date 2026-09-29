import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, apiFetch, createClientId, recoverAgentTurn, setAuthenticationFailureHandler, streamAgentMessage } from "./api";


afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  setAuthenticationFailureHandler();
});

describe("Agent stream protocol", () => {
  function responseFor(chunks: string[], close = true) {
    return new Response(new ReadableStream({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(new TextEncoder().encode(chunk));
        if (close) controller.close();
      },
    }), { headers: { "Content-Type": "text/event-stream" } });
  }

  it("creates a UUID on HTTP origins without randomUUID", () => {
    vi.stubGlobal("crypto", { getRandomValues: (bytes: Uint8Array) => bytes.fill(255) });
    expect(createClientId()).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  });

  it("handles split CRLF frames and finishes at done without waiting for EOF", async () => {
    const events: string[] = [];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(responseFor([
      'data: {"type":"message_delta","delta":"你好"}\r',
      '\n\r\ndata: {"type":"done"}\r\n\r\n',
    ], false)));
    await expect(streamAgentMessage("session", "hello", (event) => events.push(event.type), "turn"))
      .resolves.toEqual({ status: "succeeded", error: undefined });
    expect(events).toEqual(["message_delta", "done"]);
  });

  it("does not report failed execution followed by done as success", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(responseFor([
      'data: {"type":"error","error":{"message":"模型暂不可用"}}\n\n',
      'data: {"type":"done"}\n\n',
    ])));
    await expect(recoverAgentTurn("turn", () => undefined))
      .resolves.toEqual({ status: "failed", error: "模型暂不可用" });
  });

  it("uses explicit successful final status after a recovered tool error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(responseFor([
      'data: {"type":"error","message":"first attempt failed"}\n\n',
      'data: {"type":"message_done","status":"succeeded"}\n\n',
      'data: {"type":"done"}\n\n',
    ])));
    await expect(recoverAgentTurn("turn", () => undefined))
      .resolves.toEqual({ status: "succeeded", error: undefined });
  });

  it("rejects a disconnected stream so callers can recover the original turn", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(responseFor(['data: {"type":"heartbeat"}\n\n'])));
    await expect(recoverAgentTurn("turn", () => undefined)).rejects.toBeInstanceOf(ApiError);
  });

  it("times out a silent stream instead of leaving the composer locked", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(responseFor([], false)));
    const stream = recoverAgentTurn("turn", () => undefined);
    const assertion = expect(stream).rejects.toThrow("消息流长时间没有响应");
    await vi.advanceTimersByTimeAsync(45000);
    await assertion;
  });

  it("passes approval and cancellation through the message contract", async () => {
    const fetchMock = vi.fn().mockResolvedValue(responseFor(['data: {"type":"done"}\n\n']));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    await streamAgentMessage("session", "确认", () => undefined, "turn", {
      approvalId: "a".repeat(64), signal: controller.signal,
    });
    expect(fetchMock).toHaveBeenCalledWith("/api/agent/sessions/session/message", expect.objectContaining({
      signal: controller.signal,
      body: JSON.stringify({ content: "确认", turn_id: "turn", approval_id: "a".repeat(64) }),
    }));
  });
});

describe("apiFetch", () => {
  it("includes credentials and decodes JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiFetch<{ ok: boolean }>("/api/health")).resolves.toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/health",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("surfaces API error details", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "denied" }), {
          status: 403,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    await expect(apiFetch("/api/private")).rejects.toEqual(new ApiError(403, "denied"));
  });

  it("sends the initial-admin bootstrap token only when supplied", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "1", username: "admin", role: "admin" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.register("admin", "password123", "bootstrap-token-123456789012");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/auth/register",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          username: "admin",
          password: "password123",
          bootstrap_token: "bootstrap-token-123456789012",
        }),
      }),
    );
  });

  it("expires authentication for a protected 401 without intercepting login or guest checks", async () => {
    const expired = vi.fn();
    setAuthenticationFailureHandler(expired);
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => Promise.resolve(
      new Response(JSON.stringify({ detail: "Session expired" }), { status: 401 }),
    )));
    await expect(api.tasks()).rejects.toBeInstanceOf(ApiError);
    expect(expired).toHaveBeenCalledTimes(1);
    await expect(api.login("user", "wrong password")).rejects.toBeInstanceOf(ApiError);
    await expect(api.me()).rejects.toBeInstanceOf(ApiError);
    expect(expired).toHaveBeenCalledTimes(1);
  });

  it("passes pagination to the task API", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("[]"));
    vi.stubGlobal("fetch", fetchMock);
    await api.tasks({ limit: 21, offset: 100 });
    expect(fetchMock).toHaveBeenCalledWith("/api/tasks?limit=21&offset=100", expect.any(Object));
  });
});
