import type { ResearchApi, RunEvent } from "./types";
import { projectRouteKey } from "./routeKeys";
import { createAdminApi } from "./admin";

type Options = { baseUrl?: string; token: () => string | null; fetcher?: typeof fetch; onUnauthorized?: () => Promise<string | null> };

export class ApiError extends Error {
  readonly code?: string;
  readonly retryAfterSeconds?: number;

  constructor(readonly status: number, detail: unknown, retryAfterHeader?: string | null) {
    const data = detail && typeof detail === "object" ? detail as Record<string, unknown> : null;
    super(typeof detail === "string" ? detail : typeof data?.message === "string" ? data.message : `HTTP ${status}`);
    this.name = "ApiError";
    if (typeof data?.code === "string") this.code = data.code;
    if (retryAfterHeader && /^[1-9][0-9]*$/.test(retryAfterHeader)) {
      const seconds = Number(retryAfterHeader);
      if (Number.isSafeInteger(seconds)) this.retryAfterSeconds = seconds;
    }
  }
}

export function createHttpApi({ baseUrl = "/api/v1", token, fetcher = fetch, onUnauthorized }: Options): ResearchApi {
  const projectPath = (id: string) => `/g/${encodeURIComponent(projectRouteKey(id))}`;
  const chatPath = (id: string, projectId?: string | null) => `${projectId ? projectPath(projectId) : ""}/c/${encodeURIComponent(id)}`;
  const send = async (path: string, init: RequestInit = {}): Promise<Response> => {
    const cookieAuthMutation = init.method === "POST" && [
      "/auth/anonymous", "/auth/refresh", "/auth/logout",
    ].includes(path);
    let csrfToken: string | null = null;
    if (cookieAuthMutation) {
      const csrf = await fetcher(`${baseUrl}/auth/csrf`, {
        method: "GET",
        credentials: "same-origin",
        headers: { "Cache-Control": "no-store" },
      });
      if (!csrf.ok) throw new ApiError(csrf.status, csrf.statusText);
      if (csrf.status !== 204) {
        const payload = await csrf.json() as { csrf_token?: unknown };
        if (typeof payload.csrf_token !== "string" || !payload.csrf_token) {
          throw new ApiError(502, "Invalid CSRF bootstrap response");
        }
        csrfToken = payload.csrf_token;
      }
    }
    const withToken = (accessToken: string | null) => ({
      ...init,
      credentials: "same-origin" as const,
      headers: { ...(typeof init.body === "string" ? { "Content-Type": "application/json" } : {}), ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}), ...(csrfToken ? { "X-CSRF-Token": csrfToken } : {}), ...init.headers },
    });
    let response = await fetcher(`${baseUrl}${path}`, withToken(token()));
    if (response.status === 401 && onUnauthorized && !path.startsWith("/auth/")) {
      const refreshedToken = await onUnauthorized();
      if (refreshedToken) response = await fetcher(`${baseUrl}${path}`, withToken(refreshedToken));
    }
    return response;
  };
  const request = async <T>(path: string, init: RequestInit = {}): Promise<T> => {
    const response = await send(path, init);
    if (!response.ok) {
      const payload: unknown = await response.json().catch(() => null);
      const detail = payload && typeof payload === "object" && "detail" in payload ? payload.detail : response.statusText;
      throw new ApiError(response.status, detail, response.headers.get("Retry-After"));
    }
    return response.json() as Promise<T>;
  };
  const post = <T>(path: string, body: unknown) => request<T>(path, { method: "POST", body: JSON.stringify(body) });
  const remove = async (path: string) => {
    const response = await send(path, { method: "DELETE" });
    if (!response.ok) throw new ApiError(response.status, response.statusText);
  };
  return {
    ...createAdminApi(request),
    getComputeCapabilities: () => request("/compute/capabilities"),
    getComputeJobs: (capabilityId) => request(`/compute/jobs?capability_id=${encodeURIComponent(capabilityId)}`),
    submitComputeJob: (payload, key) => request("/compute/jobs", { method: "POST", body: JSON.stringify(payload), headers: { "Idempotency-Key": key } }),
    getComputeJob: (id) => request(`/compute/jobs/${encodeURIComponent(id)}`),
    cancelComputeJob: (id) => post(`/compute/jobs/${encodeURIComponent(id)}/cancel`, {}),
    startAnonymous: (captchaToken) => post("/auth/anonymous", { captcha_token: captchaToken ?? null }),
    beginGuestEmailUpgrade: (email, captchaToken) => post("/auth/upgrade/email", {
      email, captcha_token: captchaToken ?? null,
    }),
    verifyGuestEmailUpgrade: (email, code) => post("/auth/upgrade/email/verify", { email, token: code }),
    beginGuestGoogleUpgrade: () => post("/auth/upgrade/google/start", {}),
    loginDemo: (email) => post("/auth/demo", { email }),
    loginEmail: (email, password) => post("/auth/login", { email, password }),
    signupEmail: (email, password, captchaToken) => post("/auth/signup", {
      email, password, captcha_token: captchaToken ?? null,
    }),
    requestPasswordRecovery: (email, captchaToken) => post("/auth/password/recover", {
      email, captcha_token: captchaToken ?? null,
    }),
    verifyEmailCode: (email, code, kind) => post("/auth/verify", { email, token: code, type: kind }),
    updatePassword: async (accessToken, password) => {
      const response = await send("/auth/password/update", {
        method: "POST", body: JSON.stringify({ password }),
        headers: { Authorization: `Bearer ${accessToken}` },
      });
      if (!response.ok) throw new ApiError(response.status, response.statusText);
    },
    refreshAuth: () => post("/auth/refresh", {}),
    logoutAuth: async () => {
      const response = await send("/auth/logout", { method: "POST" });
      if (!response.ok) throw new Error(`Logout HTTP ${response.status}`);
    },
    googleLoginUrl: () => `${baseUrl}/auth/google/start`,
    getMe: () => request("/me"),
    updateProfile: (name) => request("/me", { method: "PATCH", body: JSON.stringify({ name }) }),
    uploadAvatar: (file) => request("/me/avatar", {
      method: "PUT", body: file, headers: { "Content-Type": file.type },
    }),
    getAvatar: async () => {
      const response = await send("/me/avatar");
      if (!response.ok) throw new ApiError(response.status, response.statusText);
      return response.blob();
    },
    deleteAvatar: () => request("/me/avatar", { method: "DELETE" }),
    getUsage: () => request("/usage"),
    getUsageEntries: () => request("/usage/entries"),
    getUsageActivity: () => request("/usage/activity?days=365"),
    getProjects: () => request("/g"),
    createProject: (name, description = "", icon) => post("/g", { name, description, ...(icon ? { icon } : {}) }),
    renameProject: (id, name) => request(projectPath(id), { method: "PATCH", body: JSON.stringify({ name }) }),
    setProjectIcon: (id, icon) => request(`${projectPath(id)}/icon`, { method: "PATCH", body: JSON.stringify({ icon }) }),
    archiveProject: (id) => remove(projectPath(id)),
    getProjectSkills: (id) => request(`${projectPath(id)}/skills`),
    setProjectSkills: (id, settings) => request(`${projectPath(id)}/skills`, { method: "PUT", body: JSON.stringify(settings) }),
    getSessions: (projectId) => request(projectId ? `${projectPath(projectId)}/c` : "/c"),
    getSession: (id, projectId) => request(chatPath(id, projectId)),
    createSession: (projectId, title, autoTitle = false) => post(projectId ? `${projectPath(projectId)}/c` : "/c", { title, ...(autoTitle ? { auto_title: true } : {}) }),
    renameSession: (id, title, projectId) => request(chatPath(id, projectId), { method: "PATCH", body: JSON.stringify({ title }) }),
    archiveSession: (id, projectId) => remove(chatPath(id, projectId)),
    moveSession: (id, targetProjectId, sourceProjectId) => request(`${chatPath(id, sourceProjectId)}/project`, { method: "PATCH", body: JSON.stringify({ project_id: targetProjectId }) }),
    getMessages: (sessionId, projectId) => request(`${chatPath(sessionId, projectId)}/messages`),
    getSkills: () => request("/skills"),
    getSkill: (id) => request(`/skills/${encodeURIComponent(id)}`),
    uploadSkill: (file, visibility) => request(
      `/skills/packages?visibility=${encodeURIComponent(visibility)}`,
      { method: "POST", body: file, headers: { "Content-Type": "application/zip" } },
    ),
    getResources: () => request("/resources"),
    getModels: () => request("/models"),
    getFiles: () => request("/files"),
    uploadFile: (file) => request(`/files/content?name=${encodeURIComponent(file.name)}`, {
      method: "PUT", body: file,
    }),
    downloadFile: async (id) => {
      const response = await send(`/files/${encodeURIComponent(id)}/download`);
      if (!response.ok) throw new ApiError(response.status, response.statusText);
      return response.blob();
    },
    deleteFile: (id) => remove(`/files/${encodeURIComponent(id)}`),
    getArtifacts: () => request("/artifacts"),
    getSessionArtifacts: (sessionId) => request(`/sessions/${encodeURIComponent(sessionId)}/artifacts`),
    getArtifactPreview: (id) => request(`/artifacts/${encodeURIComponent(id)}/preview`),
    downloadArtifact: async (id) => {
      const response = await send(`/artifacts/${encodeURIComponent(id)}/download`);
      if (!response.ok) throw new ApiError(response.status, response.statusText);
      return response.blob();
    },
    sendMessage: (sessionId, message, idempotencyKey, projectId) => request(
      `${chatPath(sessionId, projectId)}/messages`,
      { method: "POST", body: JSON.stringify(message),
        headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {} },
    ),
    cancelRun: (runId) => request(`/runs/${encodeURIComponent(runId)}`, { method: "DELETE" }),
    decideApproval: (runId, approvalId, decision) => post(
      `/runs/${encodeURIComponent(runId)}/approvals/${encodeURIComponent(approvalId)}`,
      { decision },
    ),
    async getRunEvents(runId, after) {
      const path = `/runs/${encodeURIComponent(runId)}/events?follow=false${after ? `&after=${encodeURIComponent(after)}` : ""}`;
      const response = await send(path);
      if (!response.ok) throw new Error(`Event stream HTTP ${response.status}`);
      const body = await response.text();
      return body.split("\n\n").flatMap((block): RunEvent[] => {
        const line = block.split("\n").find((part) => part.startsWith("data: "));
        return line ? [JSON.parse(line.slice(6)) as RunEvent] : [];
      });
    },
    async streamRunEvents(runId, after, onEvent, signal) {
      const path = `/runs/${encodeURIComponent(runId)}/events${after ? `?after=${encodeURIComponent(after)}` : ""}`;
      const response = await send(path, { signal });
      if (!response.ok) throw new Error(`Event stream HTTP ${response.status}`);
      if (!response.body) throw new Error("Event stream has no body");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      const consume = () => {
        let boundary: number;
        while ((boundary = buffer.indexOf("\n\n")) !== -1) {
          const frame = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);

          // Parse SSE frame: support multi-line data fields
          const lines = frame.split("\n");
          let eventData = "";

          for (const line of lines) {
            if (line.startsWith("data:")) {
              const content = line.slice(5).trimStart();
              eventData += (eventData ? "\n" : "") + content;
            }
          }

          if (eventData && !signal.aborted) {
            try {
              onEvent(JSON.parse(eventData) as RunEvent);
            } catch (e) {
              console.error("Failed to parse SSE event:", eventData, e);
            }
          }
        }
        if (buffer.length > 1_000_000) throw new Error("Event frame too large");
      };
      try {
        while (!signal.aborted) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true }).replaceAll("\r\n", "\n");
          consume();
        }
        buffer += decoder.decode();
        consume();
      } finally {
        reader.releaseLock();
      }
    },
    getMcpTools: () => request("/mcp/tools"),
    invokeMcpTool: (name, args, idempotencyKey) => request(
      `/mcp/tools/${encodeURIComponent(name)}/invoke`,
      { method: "POST", body: JSON.stringify(args),
        headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {} },
    ),
    getToolRuns: (tool) => request(`/tool-runs${tool ? `?tool=${encodeURIComponent(tool)}` : ""}`),
    saveToolRunToProject: (id, projectId) => request(`/tool-runs/${encodeURIComponent(id)}/project`, { method: "PATCH", body: JSON.stringify({ project_id: projectId }) }),
    getToolProducts: (cursor) => request(`/tool-products${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`),
    getToolProduct: (slug) => request(`/tool-products/${encodeURIComponent(slug)}`),
    startToolProductRun: (slug, actionId, args, idempotencyKey) => request(
      `/tool-products/${encodeURIComponent(slug)}/actions/${encodeURIComponent(actionId)}/runs`,
      { method: "POST", body: JSON.stringify({ arguments: args }), headers: { "Idempotency-Key": idempotencyKey } },
    ),
    getToolProductRuns: (slug) => request(`/tool-products/${encodeURIComponent(slug)}/runs`),
    getToolProductRun: (id) => request(`/tool-runs/${encodeURIComponent(id)}`),
    getToolProductRunEvents: (id, cursor) => request(`/tool-runs/${encodeURIComponent(id)}/events?cursor=${cursor}&limit=100`),
    cancelToolProductRun: (id) => post(`/tool-runs/${encodeURIComponent(id)}/cancel`, {}),
    handoffToolProductRun: (id, handoffId) => post(`/tool-runs/${encodeURIComponent(id)}/agent-handoffs/${encodeURIComponent(handoffId)}`, {}),
    submitAf3: (payload, idempotencyKey) => request("/af3/jobs", {
      method: "POST",
      body: JSON.stringify(payload),
      headers: { "Idempotency-Key": idempotencyKey },
    }),
    getAf3Job: (jobId) => request(`/af3/jobs/${encodeURIComponent(jobId)}`),
    cancelAf3Job: (jobId) => request(`/af3/jobs/${encodeURIComponent(jobId)}`, { method: "DELETE" }),
  };
}
