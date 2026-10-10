import { afterEach, describe, expect, it, vi } from "vitest";
import type { RunEvent } from "./types";
import { ApiError, createHttpApi } from "./http";

describe("HTTP Research API", () => {
  afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

  it("parses nonnegative Retry-After seconds and HTTP dates", () => {
    vi.spyOn(Date, "now").mockReturnValue(Date.parse("2026-10-10T00:00:00Z"));
    expect(new ApiError(429, { code: "AUTH_RATE_LIMITED" }, "17").retryAfterSeconds).toBe(17);
    expect(new ApiError(429, {}, "0").retryAfterSeconds).toBe(0);
    expect(new ApiError(429, {}, " 42 ").retryAfterSeconds).toBe(42);
    expect(new ApiError(429, {}, "1.5").retryAfterSeconds).toBeUndefined();
    expect(new ApiError(429, {}, "Sat, 10 Oct 2026 00:00:42 GMT").retryAfterSeconds).toBe(42);
    expect(new ApiError(503, {}, "Fri, 09 Oct 2026 00:00:00 GMT").retryAfterSeconds).toBe(0);
    expect(new ApiError(429, {}, "invalid").retryAfterSeconds).toBeUndefined();
    expect(new ApiError(429, {}, "9007199254740992").retryAfterSeconds).toBeUndefined();
  });

  it("forwards captcha proof only to protected Python mail routes", async () => {
    const fetcher = vi.fn().mockImplementation(async () => new Response(JSON.stringify({
      status: "check_email", session: null,
    }), { status: 200 }));
    const api = createHttpApi({ token: () => "guest-jwt", fetcher });

    await api.signupEmail("new@example.org", "strong-password", "signup-proof");
    await api.requestPasswordRecovery("new@example.org", "recovery-proof");
    await api.beginGuestEmailUpgrade("new@example.org", "upgrade-proof");

    expect(fetcher.mock.calls.map(([, init]) => JSON.parse(String(init?.body)))).toEqual([
      { email: "new@example.org", password: "strong-password", captcha_token: "signup-proof" },
      { email: "new@example.org", captcha_token: "recovery-proof" },
      { email: "new@example.org", captcha_token: "upgrade-proof" },
    ]);
  });

  it("starts an anonymous Python session only when requested", async () => {
    const session = { access_token: "guest-jwt", expires_in: 3600,
      user: { id: "guest-1", email: "", name: "Guest", is_anonymous: true } };
    const fetcher = vi.fn(async (input: RequestInfo | URL) => String(input).endsWith("/auth/csrf")
      ? new Response(null, { status: 204 })
      : new Response(JSON.stringify(session), { status: 200 }));
    const api = createHttpApi({ token: () => null, fetcher });

    expect(fetcher).not.toHaveBeenCalled();
    expect(await api.startAnonymous()).toEqual(session);
    expect(fetcher).toHaveBeenCalledWith("/api/v1/auth/anonymous", expect.objectContaining({
      method: "POST", credentials: "same-origin",
      body: JSON.stringify({ captcha_token: null }),
    }));
  });

  it("starts guest email linking with the current bearer token", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: "check_email" }), { status: 200 }));
    const api = createHttpApi({ token: () => "guest-jwt", fetcher });

    expect(await api.beginGuestEmailUpgrade("new@example.org")).toEqual({ status: "check_email" });
    expect(fetcher).toHaveBeenCalledWith("/api/v1/auth/upgrade/email", expect.objectContaining({
      method: "POST", body: JSON.stringify({ email: "new@example.org", captcha_token: null }),
      headers: expect.objectContaining({ Authorization: "Bearer guest-jwt" }),
    }));
  });

  it("verifies a guest email code and returns the upgraded session", async () => {
    const session = { access_token: "member-jwt", expires_in: 3600,
      user: { id: "guest-1", email: "new@example.org", name: "new", is_anonymous: false } };
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(session), { status: 200 }));
    const api = createHttpApi({ token: () => "guest-jwt", fetcher });

    expect(await api.verifyGuestEmailUpgrade("new@example.org", "123456")).toEqual(session);
    expect(fetcher).toHaveBeenCalledWith("/api/v1/auth/upgrade/email/verify", expect.objectContaining({
      method: "POST", body: JSON.stringify({ email: "new@example.org", token: "123456" }),
      headers: expect.objectContaining({ Authorization: "Bearer guest-jwt" }),
    }));
  });

  it("starts Google identity linking with the guest bearer token", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      url: "https://accounts.google.com/oauth",
    }), { status: 200 }));
    const api = createHttpApi({ token: () => "guest-jwt", fetcher });

    expect(await api.beginGuestGoogleUpgrade()).toEqual({ url: "https://accounts.google.com/oauth" });
    expect(fetcher).toHaveBeenCalledWith("/api/v1/auth/upgrade/google/start", expect.objectContaining({
      method: "POST", headers: expect.objectContaining({ Authorization: "Bearer guest-jwt" }),
    }));
  });

  it("reads user-scoped usage entries from Python", async () => {
    const rows = [{ id: "token-1", resource: "tokens", kind: "reservation", amount: 10,
      status: "posted", period: "2026-10", run_id: "run-1", job_id: null,
      created_at: "2026-10-01T00:00:00Z" }];
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(rows), { status: 200 }));
    const api = createHttpApi({ token: () => "token-1", fetcher });
    expect(await api.getUsageEntries()).toEqual(rows);
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/usage/entries");
    expect(fetcher.mock.calls[0][1].headers.Authorization).toBe("Bearer token-1");
  });
  it("streams the selected file through the Python API without JSON encoding", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: "f1", name: "notes.txt", size: 5, status: "ready" }), { status: 200 }));
    const api = createHttpApi({ token: () => "token-1", fetcher });
    const file = new File(["notes"], "notes.txt", { type: "text/plain" });
    await api.uploadFile(file);
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/files/content?name=notes.txt");
    expect(fetcher.mock.calls[0][1].body).toBe(file);
    expect(fetcher.mock.calls[0][1].headers.Authorization).toBe("Bearer token-1");
    expect(fetcher.mock.calls[0][1].headers["Content-Type"]).toBeUndefined();
  });
  it("downloads an owned artifact through the authenticated Python API", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response("data_structure", { status: 200 }));
    const api = createHttpApi({ token: () => "token-1", fetcher });
    const blob = await api.downloadArtifact("artifact-1");
    expect(await blob.text()).toBe("data_structure");
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/artifacts/artifact-1/download");
    expect(fetcher.mock.calls[0][1].headers.Authorization).toBe("Bearer token-1");
  });
  it("sends bearer auth and structured message refs", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ run_id: "run-1" }), { status: 200, headers: { "content-type": "application/json" } }));
    const api = createHttpApi({ baseUrl: "http://test/api/v1", token: () => "token-1", fetcher });
    const result = await api.sendMessage("session-1", { content: "Analyze", attachments: [{ id: "f1", name: "paper.pdf" }], skills: [], resources: [] });
    expect(result.run_id).toBe("run-1");
    expect(fetcher).toHaveBeenCalledWith("http://test/api/v1/c/session-1/messages", expect.objectContaining({ headers: expect.objectContaining({ Authorization: "Bearer token-1" }) }));
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toMatchObject({ attachments: [{ id: "f1" }] });
  });

  it("passes the message idempotency key to Python", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ run_id: "run-1" }), { status: 200 }));
    const api = createHttpApi({ token: () => "token-1", fetcher });
    await api.sendMessage("session-1", { content: "hello", attachments: [], skills: [], resources: [] }, "send-001");
    expect(fetcher).toHaveBeenCalledWith(
      "/api/v1/c/session-1/messages",
      expect.objectContaining({ headers: expect.objectContaining({ "Idempotency-Key": "send-001" }) }),
    );
  });

  it("passes an MCP invocation idempotency key to Python", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      tool: "search_pdb", status: "completed", result: { hits: [] }, run_id: "toolrun-1",
    }), { status: 200 }));
    const api = createHttpApi({ token: () => "token-1", fetcher });
    await api.invokeMcpTool("search_pdb", { query: "RNA" }, "mcp-001");
    expect(fetcher).toHaveBeenCalledWith(
      "/api/v1/mcp/tools/search_pdb/invoke",
      expect.objectContaining({ headers: expect.objectContaining({ "Idempotency-Key": "mcp-001" }) }),
    );
  });

  it("passes caller supplied AF3 input and GPU estimate with an idempotency key", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      id: "job-1", status: "queued", progress: 0, estimated_gpu_minutes: 20,
      actual_gpu_minutes: null, run_id: null, artifacts: [], fold_input: null, simulation: true,
    }), { status: 200 }));
    const api = createHttpApi({ token: () => "token-1", fetcher });
    const fold_input = {
      name: "small protein", modelSeeds: [1],
      sequences: [{ protein: { id: "A", sequence: "PVLSCGEWQL" } }],
      dialect: "alphafold3" as const, version: 4 as const,
    };
    await api.submitAf3({ estimated_gpu_minutes: 35, run_id: "run-1", fold_input }, "af3-001");
    expect(fetcher).toHaveBeenCalledWith(
      "/api/v1/af3/jobs",
      expect.objectContaining({ headers: expect.objectContaining({ "Idempotency-Key": "af3-001" }) }),
    );
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({
      estimated_gpu_minutes: 35, run_id: "run-1", fold_input,
    });
  });

  it("sends an approval decision through the Python control API", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      approval_id: "approval-1", status: "approved", job_id: "job-1",
    }), { status: 200 }));
    const api = createHttpApi({ token: () => "token-1", fetcher });
    const result = await api.decideApproval("run-1", "approval-1", "approved");
    expect(result.job_id).toBe("job-1");
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/runs/run-1/approvals/approval-1");
    expect(JSON.parse(String(fetcher.mock.calls[0][1].body))).toEqual({ decision: "approved" });
  });

  it("creates projects and sessions through the Python API", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: "project-1", name: "RNA", description: "" }), { status: 201 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: "session-1", project_id: "project-1", title: "Study", status: "idle" }), { status: 201 }));
    const api = createHttpApi({ token: () => "token-1", fetcher });
    const project = await api.createProject("RNA");
    const session = await api.createSession(project.id, "Study");
    expect(session.project_id).toBe(project.id);
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/g");
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ name: "RNA", description: "" });
    expect(fetcher.mock.calls[1][0]).toBe("/api/v1/g/g-p-1/c");
    expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ title: "Study" });
  });

  it("resolves one project session at its scoped API path", async () => {
    const session = { id: "session-1", project_id: "project-1", title: "Study", status: "idle" };
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(session), { status: 200 }));
    const api = createHttpApi({ token: () => "token-1", fetcher });
    expect(await api.getSession(session.id, session.project_id)).toEqual(session);
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/g/g-p-1/c/session-1");
  });

  it("sends a selected project icon and updates it later", async () => {
    const project = { id: "project-1", name: "RNA", description: "", icon: "dna" };
    const fetcher = vi.fn().mockImplementation(async () => new Response(JSON.stringify(project), { status: 200 }));
    const api = createHttpApi({ token: () => "token-1", fetcher });
    await api.createProject("RNA", "", "dna");
    await api.setProjectIcon(project.id, "microscope");
    expect(JSON.parse(String(fetcher.mock.calls[0][1].body))).toEqual({ name: "RNA", description: "", icon: "dna" });
    expect(fetcher.mock.calls[1][0]).toBe("/api/v1/g/g-p-1/icon");
    expect(fetcher.mock.calls[1][1].method).toBe("PATCH");
    expect(JSON.parse(String(fetcher.mock.calls[1][1].body))).toEqual({ icon: "microscope" });
  });

  it("parses SSE frames and sends the resume cursor", async () => {
    const event = { id: "3", run_id: "run-1", type: "run.completed", data: { status: "completed" } };
    const fetcher = vi.fn().mockResolvedValue(new Response(`id: 3\nevent: run.completed\ndata: ${JSON.stringify(event)}\n\n`, { status: 200, headers: { "content-type": "text/event-stream" } }));
    const api = createHttpApi({ baseUrl: "/api/v1", token: () => "token-1", fetcher });
    expect(await api.getRunEvents("run-1", "2")).toEqual([event]);
    expect(fetcher).toHaveBeenCalledWith("/api/v1/runs/run-1/events?follow=false&after=2", expect.objectContaining({ headers: { Authorization: "Bearer token-1" } }));
  });

  it("delivers an SSE event before the connection closes and resumes from a cursor", async () => {
    const event = { id: "3", run_id: "run-1", type: "message.delta", data: { delta: "hello" } } as const;
    let finish!: () => void;
    const holdOpen = new Promise<void>((resolve) => { finish = resolve; });
    const encoder = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      async start(controller) {
        controller.enqueue(encoder.encode(`id: 3\nevent: message.delta\ndata: ${JSON.stringify(event).slice(0, 38)}`));
        controller.enqueue(encoder.encode(`${JSON.stringify(event).slice(38)}\n\n`));
        await holdOpen;
        controller.close();
      },
    });
    const fetcher = vi.fn().mockResolvedValue(new Response(body, { status: 200 }));
    const api = createHttpApi({ token: () => "token-1", fetcher });
    const onEvent = vi.fn();
    const controller = new AbortController();

    const reading = api.streamRunEvents("run-1", "2", onEvent, controller.signal);
    await vi.waitFor(() => expect(onEvent).toHaveBeenCalledWith(event));
    expect(fetcher).toHaveBeenCalledWith(
      "/api/v1/runs/run-1/events?after=2",
      expect.objectContaining({ signal: controller.signal }),
    );
    finish();
    await reading;
  });

  it("logs in through Python and refreshes an expired access token", async () => {
    let token: string | null = null;
    const paths: string[] = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      paths.push(path);
      const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } });
      if (path.endsWith("/auth/login")) {
        expect(JSON.parse(String(init?.body))).toEqual({ email: "alice@example.org", password: "secret" });
        return json({ access_token: "first-jwt", expires_in: 3600, user: { id: "alice", email: "alice@example.org", name: "Alice" } });
      }
      if (path.endsWith("/auth/csrf")) return json({ csrf_token: "signed-csrf" });
      if (path.endsWith("/auth/refresh")) return json({ access_token: "fresh-jwt", expires_in: 3600, user: { id: "alice", email: "alice@example.org", name: "Alice" } });
      if (path.endsWith("/usage") && init?.headers && "Authorization" in init.headers) {
        if (init.headers.Authorization === "Bearer first-jwt") return json({ detail: "expired" }, 401);
        expect(init.headers.Authorization).toBe("Bearer fresh-jwt");
        return json({ tokens: { remaining: 90 } });
      }
      return json({ detail: "not found" }, 404);
    });
    const api: ReturnType<typeof createHttpApi> = createHttpApi({
      token: () => token,
      fetcher: fetcher as typeof fetch,
      onUnauthorized: async () => { const session = await api.refreshAuth(); token = session.access_token; return token; },
    });
    token = (await api.loginEmail("alice@example.org", "secret")).access_token;
    const usage = await api.getUsage();
    expect(usage.tokens.remaining).toBe(90);
    expect(paths).toEqual([
      "/api/v1/auth/login", "/api/v1/usage", "/api/v1/auth/csrf",
      "/api/v1/auth/refresh", "/api/v1/usage",
    ]);
  });

  it("uses only Python routes for signup, recovery, OTP, and password update", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ status: "check_email", session: null }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ status: "email_sent" }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ access_token: "verified", expires_in: 3600, user: { id: "alice", email: "alice@example.org", name: "Alice" } }), { status: 200 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    const api = createHttpApi({ token: () => null, fetcher });
    expect((await api.signupEmail("alice@example.org", "strong-password")).status).toBe("check_email");
    expect((await api.requestPasswordRecovery("alice@example.org")).status).toBe("email_sent");
    const verified = await api.verifyEmailCode("alice@example.org", "123456", "recovery");
    await api.updatePassword(verified.access_token, "new-strong-password");
    expect(fetcher.mock.calls.map(([path]) => path)).toEqual([
      "/api/v1/auth/signup", "/api/v1/auth/password/recover",
      "/api/v1/auth/verify", "/api/v1/auth/password/update",
    ]);
    expect(fetcher.mock.calls[3][1].headers.Authorization).toBe("Bearer verified");
  });

  it("accepts an empty 204 response when Python logs out", async () => {
    const fetcher = vi.fn(async (input: RequestInfo | URL) => String(input).endsWith("/auth/csrf")
      ? new Response(JSON.stringify({ csrf_token: "signed-csrf" }), { status: 200 })
      : new Response(null, { status: 204 }));
    const api = createHttpApi({ token: () => "signed-jwt", fetcher: fetcher as typeof fetch });
    await expect(api.logoutAuth()).resolves.toBeUndefined();
    expect(fetcher).toHaveBeenCalledWith(
      "/api/v1/auth/logout",
      expect.objectContaining({
        method: "POST",
        credentials: "same-origin",
        headers: expect.objectContaining({ "X-CSRF-Token": "signed-csrf" }),
      }),
    );
  });

  it("bootstraps a fresh CSRF token before every refresh-cookie mutation", async () => {
    const paths: string[] = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      void _init;
      const path = String(input);
      paths.push(path);
      if (path.endsWith("/auth/csrf")) {
        return new Response(JSON.stringify({ csrf_token: `csrf-${paths.length}` }), { status: 200 });
      }
      if (path.endsWith("/auth/anonymous")) {
        return new Response(JSON.stringify({ access_token: "guest", expires_in: 3600,
          user: { id: "guest", email: "", name: "Guest", is_anonymous: true } }), { status: 200 });
      }
      return new Response(JSON.stringify({ access_token: "fresh", expires_in: 3600,
        user: { id: "guest", email: "", name: "Guest", is_anonymous: true } }), { status: 200 });
    });
    const api = createHttpApi({ token: () => null, fetcher: fetcher as typeof fetch });

    await api.startAnonymous();
    await api.refreshAuth();

    expect(paths).toEqual([
      "/api/v1/auth/csrf", "/api/v1/auth/anonymous",
      "/api/v1/auth/csrf", "/api/v1/auth/refresh",
    ]);
    expect(fetcher.mock.calls[1]?.[1]).toEqual(expect.objectContaining({
      headers: expect.objectContaining({ "X-CSRF-Token": "csrf-1" }),
    }));
    expect(fetcher.mock.calls[3]?.[1]).toEqual(expect.objectContaining({
      headers: expect.objectContaining({ "X-CSRF-Token": "csrf-3" }),
    }));
  });

  it("keeps stable API error codes for localized UI messages", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ detail: {
      code: "TOKEN_QUOTA_EXCEEDED", message: "Monthly token quota exhausted",
    } }), { status: 409 }));
    const api = createHttpApi({ token: () => "signed-jwt", fetcher: fetcher as typeof fetch });
    await expect(api.sendMessage("session-1", { content: "hello", attachments: [], skills: [], resources: [] }))
      .rejects.toMatchObject({ status: 409, code: "TOKEN_QUOTA_EXCEEDED" } satisfies Partial<ApiError>);
  });

  it("keeps server Retry-After on API errors and never retries auth requests", async () => {
    const fetcher = vi.fn(async () => new Response(
      JSON.stringify({ detail: { code: "AUTH_RATE_LIMITED" } }),
      { status: 429, headers: { "Retry-After": "42" } },
    ));
    const api = createHttpApi({ token: () => null, fetcher: fetcher as typeof fetch });

    await expect(api.signupEmail("a@example.org", "password", "proof"))
      .rejects.toMatchObject({ status: 429, code: "AUTH_RATE_LIMITED", retryAfterSeconds: 42 });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("cancels an owned run through the Python API", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ run_id: "run-1", status: "cancelled" }), { status: 200 },
    ));
    const api = createHttpApi({ token: () => "token-1", fetcher });
    expect(await api.cancelRun("run-1")).toEqual({ run_id: "run-1", status: "cancelled" });
    expect(fetcher).toHaveBeenCalledWith(
      "/api/v1/runs/run-1",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it.each(["snapshot", "stream"])("preserves status, detail and Retry-After for %s errors", async (mode) => {
    const api = createHttpApi({ token: () => null, fetcher: vi.fn(async () => new Response(
      JSON.stringify({ detail: { code: "EVENTS_RATE_LIMITED", message: "Please wait" } }),
      { status: 429, headers: { "Retry-After": "42" } },
    )) });
    const reading = mode === "snapshot" ? api.getRunEvents("run-1")
      : api.streamRunEvents("run-1", undefined, vi.fn(), new AbortController().signal);
    await expect(reading).rejects.toMatchObject({ name: "ApiError", status: 429,
      message: "Please wait", code: "EVENTS_RATE_LIMITED", retryAfterSeconds: 42 });
  });

  it.each(["snapshot", "stream"])("preserves an HTTP date Retry-After on %s errors", async (mode) => {
    vi.spyOn(Date, "now").mockReturnValue(Date.parse("2026-10-10T00:00:00Z"));
    const api = createHttpApi({ token: () => null, fetcher: vi.fn(async () => new Response("Unavailable", {
      status: 503, statusText: "Service Unavailable", headers: { "Retry-After": "Sat, 10 Oct 2026 00:00:42 GMT" },
    })) });
    const reading = mode === "snapshot" ? api.getRunEvents("run-1")
      : api.streamRunEvents("run-1", undefined, vi.fn(), new AbortController().signal);
    await expect(reading).rejects.toMatchObject({ name: "ApiError", status: 503,
      message: "Service Unavailable", retryAfterSeconds: 42 });
  });

  it.each(["snapshot", "stream"])("handles cross-chunk CRLF, comments and multiline JSON for %s", async (mode) => {
    const event: RunEvent = { id: "3", run_id: "run-1", type: "message.delta", data: { delta: "中文🧬" } };
    const encoded = new TextEncoder().encode(`: heartbeat\r\nid: 3\r\ndata: {"id":"3","run_id":"run-1",\r\ndata: "type":"message.delta","data":{"delta":"中文🧬"}}\r\n\r\n`);
    const api = createHttpApi({ token: () => null, fetcher: vi.fn(async () => byteResponse(encoded)) });
    const received: RunEvent[] = [];
    if (mode === "snapshot") received.push(...await api.getRunEvents("run-1"));
    else await api.streamRunEvents("run-1", undefined, (item) => received.push(item), new AbortController().signal);
    expect(received).toEqual([event]);
  });

  it.each(["snapshot", "stream"])("skips bad JSON and continues to the next frame for %s", async (mode) => {
    const log = vi.spyOn(console, "error").mockImplementation(() => {});
    const event: RunEvent = { id: "2", run_id: "run-1", type: "message.delta", data: { delta: "valid" } };
    const api = createHttpApi({ token: () => null, fetcher: vi.fn(async () => new Response(
      `data: invalid json\n\ndata: ${JSON.stringify(event)}\n\n`,
    )) });
    const received: RunEvent[] = [];
    if (mode === "snapshot") received.push(...await api.getRunEvents("run-1"));
    else await api.streamRunEvents("run-1", undefined, (item) => received.push(item), new AbortController().signal);
    expect(received).toEqual([event]);
    expect(log).toHaveBeenCalledTimes(1);
  });

  it.each(["snapshot", "stream"])("rejects a complete oversized frame before delivering it for %s", async (mode) => {
    const event: RunEvent = { id: "1", run_id: "run-1", type: "message.delta", data: { delta: "x".repeat(1_000_000) } };
    const onEvent = vi.fn();
    const api = createHttpApi({ token: () => null, fetcher: vi.fn(async () => new Response(`data: ${JSON.stringify(event)}\n\n`)) });
    const reading = mode === "snapshot" ? api.getRunEvents("run-1")
      : api.streamRunEvents("run-1", undefined, onEvent, new AbortController().signal);
    await expect(reading).rejects.toThrow("Event frame too large");
    expect(onEvent).not.toHaveBeenCalled();
  });

  it("rejects an oversized partial frame and cancels the stream before EOF", async () => {
    const cancel = vi.fn();
    const body = new ReadableStream<Uint8Array>({ start(controller) {
      controller.enqueue(new TextEncoder().encode(`data: ${"中".repeat(333_332)}`));
    }, cancel });
    const api = createHttpApi({ token: () => null, fetcher: vi.fn(async () => new Response(body)) });
    const onEvent = vi.fn();
    await expect(api.streamRunEvents("run-1", undefined, onEvent, new AbortController().signal))
      .rejects.toThrow("Event frame too large");
    expect(onEvent).not.toHaveBeenCalled();
    expect(cancel).toHaveBeenCalledTimes(1);
    expect(body.locked).toBe(false);
  });

  it.each(["same chunk", "separate chunks"])("delivers earlier tool, plan and artifact frames before an oversized tail in %s", async (partition) => {
    const events: RunEvent[] = [
      { id: "1", run_id: "run-1", type: "tool.finished", data: {
        tool_call_id: "call-1", tool: "read_file", status: "completed", summary: "Read file",
      } },
      { id: "2", run_id: "run-1", type: "plan.updated", data: { steps: [
        { id: "inspect", title: "Inspect", status: "completed" },
      ] } },
      { id: "3", run_id: "run-1", type: "artifact.created", data: {
        artifact_id: "artifact-1", name: "report.md", kind: "document",
      } },
    ];
    const prefix = events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join("");
    const tail = `data: ${"x".repeat(1_000_000)}\n\n`;
    const body = new ReadableStream<Uint8Array>({ start(controller) {
      for (const chunk of partition === "same chunk" ? [prefix + tail] : [prefix, tail]) {
        controller.enqueue(new TextEncoder().encode(chunk));
      }
      controller.close();
    } });
    const api = createHttpApi({ token: () => null, fetcher: vi.fn(async () => new Response(body)) });
    const received: RunEvent[] = [];
    await expect(api.streamRunEvents("run-1", undefined, (event) => received.push(event), new AbortController().signal))
      .rejects.toThrow("Event frame too large");
    expect(received).toEqual(events);
    expect(body.locked).toBe(false);
  });

  it("propagates the consumer error from an accepted prefix before its oversized tail", async () => {
    const cancel = vi.fn();
    const body = new ReadableStream<Uint8Array>({ start(controller) {
      controller.enqueue(new TextEncoder().encode(`data: {"id":"1"}\n\ndata: ${"x".repeat(1_000_000)}`));
    }, cancel });
    const api = createHttpApi({ token: () => null, fetcher: vi.fn(async () => new Response(body)) });
    const error = new Error("consumer failed");
    await expect(api.streamRunEvents("run-1", undefined, () => { throw error; }, new AbortController().signal))
      .rejects.toBe(error);
    expect(cancel).toHaveBeenCalledTimes(1);
    expect(body.locked).toBe(false);
  });

  it("quietly aborts on an accepted terminal event before its oversized tail", async () => {
    const cancel = vi.fn();
    const event: RunEvent = { id: "1", run_id: "run-1", type: "run.completed", data: { status: "completed" } };
    const body = new ReadableStream<Uint8Array>({ start(controller) {
      controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\ndata: ${"x".repeat(1_000_000)}`));
    }, cancel });
    const api = createHttpApi({ token: () => null, fetcher: vi.fn(async () => new Response(body)) });
    const controller = new AbortController();
    const received: RunEvent[] = [];
    await expect(api.streamRunEvents("run-1", undefined, (item) => {
      received.push(item);
      controller.abort();
    }, controller.signal)).resolves.toBeUndefined();
    expect(received).toEqual([event]);
    expect(cancel).toHaveBeenCalledTimes(1);
    expect(body.locked).toBe(false);
  });

  it("propagates consumer errors and cancels the reader", async () => {
    const log = vi.spyOn(console, "error").mockImplementation(() => {});
    const cancel = vi.fn();
    const body = new ReadableStream<Uint8Array>({
      start(controller) { controller.enqueue(new TextEncoder().encode('data: {"id":"1"}\n\n')); }, cancel,
    });
    const api = createHttpApi({ token: () => null, fetcher: vi.fn(async () => new Response(body)) });
    const error = new Error("consumer failed");
    await expect(api.streamRunEvents("run-1", undefined, () => { throw error; }, new AbortController().signal)).rejects.toBe(error);
    expect(cancel).toHaveBeenCalledTimes(1);
    expect(body.locked).toBe(false);
    expect(log).not.toHaveBeenCalled();
  });

  it("cancels a pending read on abort and never delivers subsequent events", async () => {
    const cancel = vi.fn();
    const body = new ReadableStream<Uint8Array>({ cancel });
    const api = createHttpApi({ token: () => null, fetcher: vi.fn(async () => new Response(body)) });
    const controller = new AbortController();
    const onEvent = vi.fn();
    const reading = api.streamRunEvents("run-1", undefined, onEvent, controller.signal);
    await Promise.resolve();
    controller.abort();
    await reading;
    expect(cancel).toHaveBeenCalledTimes(1);
    expect(body.locked).toBe(false);
    expect(onEvent).not.toHaveBeenCalled();
  });

  it("reports a healthy EOF using established reader time after comment-only heartbeats", async () => {
    vi.useFakeTimers();
    const body = new ReadableStream<Uint8Array>({ start(controller) {
      controller.enqueue(new TextEncoder().encode(": heartbeat\n\n"));
      setTimeout(() => controller.close(), 45_000);
    } });
    const api = createHttpApi({ token: () => null, fetcher: vi.fn(async () => new Response(body)) });
    const onEvent = vi.fn();
    const reading = api.streamRunEvents("run-1", undefined, onEvent, new AbortController().signal)
      .then(() => undefined, (error: unknown) => error);
    await vi.advanceTimersByTimeAsync(45_000);
    expect(await reading).toMatchObject({ name: "SseTransportError", healthy: true, readerDurationMs: 45_000 });
    expect(onEvent).not.toHaveBeenCalled();
    expect(body.locked).toBe(false);
  });

  it("excludes slow response headers from reader failure health and preserves its cause", async () => {
    vi.useFakeTimers();
    const error = new TypeError("Short connection interrupted");
    const api = createHttpApi({ token: () => null, fetcher: vi.fn(async () => {
      await new Promise((resolve) => setTimeout(resolve, 45_000));
      return new Response(new ReadableStream<Uint8Array>({ start(controller) {
        controller.enqueue(new TextEncoder().encode(": heartbeat\n\n"));
        setTimeout(() => controller.error(error), 1_000);
      } }));
    }) });
    const reading = api.streamRunEvents("run-1", undefined, vi.fn(), new AbortController().signal)
      .then(() => undefined, (caught: unknown) => caught);
    await vi.advanceTimersByTimeAsync(46_000);
    expect(await reading).toMatchObject({ name: "SseTransportError", healthy: false, readerDurationMs: 1_000, cause: error });
  });

  it("preserves consumer errors after a long healthy reader without transport wrapping", async () => {
    vi.useFakeTimers();
    const error = new TypeError("Consumer interrupted");
    const body = new ReadableStream<Uint8Array>({ start(controller) {
      controller.enqueue(new TextEncoder().encode(": heartbeat\n\n"));
      setTimeout(() => controller.enqueue(new TextEncoder().encode('data: {"id":"1"}\n\n')), 45_000);
    } });
    const api = createHttpApi({ token: () => null, fetcher: vi.fn(async () => new Response(body)) });
    const reading = api.streamRunEvents("run-1", undefined, () => { throw error; }, new AbortController().signal)
      .then(() => undefined, (caught: unknown) => caught);
    await vi.advanceTimersByTimeAsync(45_000);
    expect(await reading).toBe(error);
    expect(body.locked).toBe(false);
  });

  it("keeps a terminal EOF successful after a long established reader", async () => {
    vi.useFakeTimers();
    const event: RunEvent = { id: "1", run_id: "run-1", type: "run.completed", data: { status: "completed" } };
    const body = new ReadableStream<Uint8Array>({ start(controller) {
      controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`));
      setTimeout(() => controller.close(), 45_000);
    } });
    const api = createHttpApi({ token: () => null, fetcher: vi.fn(async () => new Response(body)) });
    const received: RunEvent[] = [];
    const reading = api.streamRunEvents("run-1", undefined, (item) => received.push(item), new AbortController().signal)
      .then(() => undefined, (error: unknown) => error);
    await vi.advanceTimersByTimeAsync(45_000);
    expect(await reading).toBeUndefined();
    expect(received).toEqual([event]);
    expect(body.locked).toBe(false);
  });
});

function byteResponse(bytes: Uint8Array): Response {
  return new Response(new ReadableStream<Uint8Array>({ start(controller) {
    for (const byte of bytes) controller.enqueue(new Uint8Array([byte]));
    controller.close();
  } }));
}

it("requests bounded message pages without changing legacy message-list URLs", async () => {
  const fetcher = vi.fn().mockImplementation(async () => Response.json({ items: [], next_before: null }));
  const api = createHttpApi({ token: () => "token", fetcher });
  await api.getMessagePage!("session/1", null, { limit: 50, before: "opaque/id" });
  expect(String(fetcher.mock.calls[0][0])).toBe("/api/v1/c/session%2F1/messages/page?limit=50&before=opaque%2Fid");
  await api.getMessagePage!("session-2", "project-alice", { limit: 50 });
  expect(String(fetcher.mock.calls[1][0])).toMatch(/\/g\/[^/]+\/c\/session-2\/messages\/page\?limit=50$/);
  await api.getMessages("session-2");
  expect(String(fetcher.mock.calls[2][0])).toBe("/api/v1/c/session-2/messages");
});

it("falls back only for a missing initial page endpoint and never masks forbidden or foreign cursor errors", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ detail: "Missing" }, { status: 404 })).mockResolvedValueOnce(Response.json([]));
  const api = createHttpApi({ token: () => "token", fetcher });
  expect(await api.getMessagePage!("session-1")).toEqual({ items: [], next_before: null });
  expect(fetcher).toHaveBeenCalledTimes(2);
  fetcher.mockReset().mockResolvedValue(Response.json({ detail: "Forbidden" }, { status: 403 }));
  await expect(api.getMessagePage!("session-1")).rejects.toMatchObject({ status: 403 });
  expect(fetcher).toHaveBeenCalledTimes(1);
  fetcher.mockReset().mockResolvedValue(Response.json({ detail: "Foreign cursor" }, { status: 404 }));
  await expect(api.getMessagePage!("session-1", null, { before: "foreign" })).rejects.toMatchObject({ status: 404 });
  expect(fetcher).toHaveBeenCalledTimes(1);
});
