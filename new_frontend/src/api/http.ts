import type { ResearchApi, RunEvent } from "./types";
import { projectRouteKey } from "./routeKeys";
import { createAdminApi } from "./admin";

type Options = { baseUrl?: string; token: () => string | null; fetcher?: typeof fetch; onUnauthorized?: () => Promise<string | null> };

export class ApiError extends Error {
  readonly code?: string;

  constructor(readonly status: number, detail: unknown) {
    const data = detail && typeof detail === "object" ? detail as Record<string, unknown> : null;
    super(typeof detail === "string" ? detail : typeof data?.message === "string" ? data.message : `HTTP ${status}`);
    this.name = "ApiError";
    if (typeof data?.code === "string") this.code = data.code;
  }
}

export function createHttpApi({ baseUrl = "/api/v1", token, fetcher = fetch, onUnauthorized }: Options): ResearchApi {
  const projectPath = (id: string) => `/g/${encodeURIComponent(projectRouteKey(id))}`;
  const chatPath = (id: string, projectId?: string | null) => `${projectId ? projectPath(projectId) : ""}/c/${encodeURIComponent(id)}`;
  const send = async (path: string, init: RequestInit = {}): Promise<Response> => {
    const withToken = (accessToken: string | null) => ({
      ...init,
      credentials: "same-origin" as const,
      headers: { ...(typeof init.body === "string" ? { "Content-Type": "application/json" } : {}), ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}), ...init.headers },
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
      throw new ApiError(response.status, detail);
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
    startAnonymous: (captchaToken) => post("/auth/anonymous", { captcha_token: captchaToken ?? null }),
    beginGuestEmailUpgrade: (email) => post("/auth/upgrade/email", { email }),
    verifyGuestEmailUpgrade: (email, code) => post("/auth/upgrade/email/verify", { email, token: code }),
    beginGuestGoogleUpgrade: () => post("/auth/upgrade/google/start", {}),
    loginDemo: (email) => post("/auth/demo", { email }),
    loginEmail: (email, password) => post("/auth/login", { email, password }),
    signupEmail: (email, password) => post("/auth/signup", { email, password }),
    requestPasswordRecovery: (email) => post("/auth/password/recover", { email }),
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
    getUsage: () => request("/usage"),
    getUsageEntries: () => request("/usage/entries"),
    getProjects: () => request("/g"),
    createProject: (name, description = "", icon) => post("/g", { name, description, ...(icon ? { icon } : {}) }),
    renameProject: (id, name) => request(projectPath(id), { method: "PATCH", body: JSON.stringify({ name }) }),
    setProjectIcon: (id, icon) => request(`${projectPath(id)}/icon`, { method: "PATCH", body: JSON.stringify({ icon }) }),
    archiveProject: (id) => remove(projectPath(id)),
    getProjectSkills: (id) => request(`${projectPath(id)}/skills`),
    setProjectSkills: (id, settings) => request(`${projectPath(id)}/skills`, { method: "PUT", body: JSON.stringify(settings) }),
    getSessions: (projectId) => request(projectId ? `${projectPath(projectId)}/c` : "/c"),
    getSession: (id, projectId) => request(chatPath(id, projectId)),
    createSession: (projectId, title) => post(projectId ? `${projectPath(projectId)}/c` : "/c", { title }),
    renameSession: (id, title, projectId) => request(chatPath(id, projectId), { method: "PATCH", body: JSON.stringify({ title }) }),
    archiveSession: (id, projectId) => remove(chatPath(id, projectId)),
    moveSession: (id, targetProjectId, sourceProjectId) => request(`${chatPath(id, sourceProjectId)}/project`, { method: "PATCH", body: JSON.stringify({ project_id: targetProjectId }) }),
    getMessages: (sessionId, projectId) => request(`${chatPath(sessionId, projectId)}/messages`),
    getSkills: () => request("/skills"),
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
          const data = frame.split("\n").filter((line) => line.startsWith("data:"))
            .map((line) => line.slice(5).trimStart()).join("\n");
          if (data && !signal.aborted) onEvent(JSON.parse(data) as RunEvent);
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
    submitAf3: (payload, idempotencyKey) => request("/af3/jobs", {
      method: "POST",
      body: JSON.stringify(payload),
      headers: { "Idempotency-Key": idempotencyKey },
    }),
    getAf3Job: (jobId) => request(`/af3/jobs/${encodeURIComponent(jobId)}`),
    cancelAf3Job: (jobId) => request(`/af3/jobs/${encodeURIComponent(jobId)}`, { method: "DELETE" }),
  };
}
