export type User = {
  id: string;
  username: string;
  role: "admin" | "user" | string;
};

export type RegistrationStatus = {
  enabled: boolean;
  mode: "open" | "disabled" | string;
  first_user: boolean;
  requires_bootstrap_token: boolean;
};

export type AgentSession = {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
};

export type AgentMessage = {
  id: string;
  role: "user" | "assistant" | string;
  content: string;
  created_at: string;
  metadata: Record<string, unknown>;
};

export type ToolSpec = {
  name: string;
  description: string;
  long_running: boolean;
};

export type Task = {
  id: string;
  session_id: string | null;
  tool_call_id: string | null;
  task_type: string;
  status: string;
  progress: number;
  error_type: string | null;
  error_message: string | null;
  input: Record<string, unknown> | null;
  output: Record<string, unknown> | null;
  artifacts: TaskArtifact[];
  retry_of_task_id: string | null;
  retry_task_id: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export type TaskArtifact = {
  id: string;
  kind: string;
  filename: string;
  mime_type: string | null;
  size_bytes: number;
  download_url: string;
};

export type ActiveAgentTurn = {
  turn_id: string;
  client_turn_id: string;
  status: string;
  user_content: string;
  error_code: string | null;
  created_at: string;
  updated_at: string;
};

export type AdminUser = {
  id: string;
  username: string;
  role: string;
  disabled: boolean;
  active_sessions: number;
  created_at: string;
};

export type AdminMetrics = {
  users: number;
  active_sessions: number;
  tasks_by_status: Record<string, number>;
  artifacts: number;
  active_agent_turns: number;
};

export type DoctorCheck = {
  name: string;
  status: "ok" | "warn" | "fail" | string;
  detail: string;
};

export type DoctorReport = {
  overall: "ok" | "warn" | "fail" | string;
  fail_count: number;
  warn_count: number;
  checks: DoctorCheck[];
};

export type StreamEvent = {
  type: string;
  [key: string]: unknown;
};

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// randomUUID is unavailable on ordinary HTTP origins, but getRandomValues is
// supported there. Keep request IDs cryptographically random in either case.
export function createClientId(): string {
  if (typeof globalThis.crypto?.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  const bytes = new Uint8Array(16);
  globalThis.crypto.getRandomValues(bytes);
  bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x40;
  bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

async function parseError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body.detail === "string") return body.detail;
    return JSON.stringify(body);
  } catch {
    return response.statusText || "Request failed";
  }
}

let authenticationFailureHandler: (() => Promise<void> | void) | undefined;
let authenticationFailure: Promise<void> | undefined;

export function setAuthenticationFailureHandler(handler?: () => Promise<void> | void) {
  authenticationFailureHandler = handler;
}

async function throwResponseError(response: Response, path: string): Promise<never> {
  if (response.status === 401 && !path.startsWith("/api/auth/") && authenticationFailureHandler) {
    // Multiple polling requests may expire together. Navigate once; login/me
    // failures remain with their own caller so guest pages cannot redirect-loop.
    authenticationFailure ??= Promise.resolve().then(authenticationFailureHandler).finally(() => {
      authenticationFailure = undefined;
    });
    await authenticationFailure;
  }
  throw new ApiError(response.status, await parseError(response));
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init.headers || {}),
    },
  });
  if (!response.ok) {
    return throwResponseError(response, path);
  }
  return response.json() as Promise<T>;
}

export const api = {
  me: () => apiFetch<User>("/api/auth/me"),
  registrationStatus: () => apiFetch<RegistrationStatus>("/api/auth/registration"),
  login: (username: string, password: string) =>
    apiFetch<User>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  register: (username: string, password: string, bootstrapToken?: string) =>
    apiFetch<User>("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({
        username,
        password,
        ...(bootstrapToken ? { bootstrap_token: bootstrapToken } : {}),
      }),
    }),
  logout: () =>
    apiFetch<{ ok: boolean }>("/api/auth/logout", {
      method: "POST",
      body: JSON.stringify({}),
    }),
  sessions: () => apiFetch<AgentSession[]>("/api/agent/sessions"),
  createSession: (title?: string) =>
    apiFetch<AgentSession>("/api/agent/sessions", {
      method: "POST",
      body: JSON.stringify({ title }),
    }),
  sessionHistory: (sessionId: string) =>
    apiFetch<AgentMessage[]>(`/api/agent/sessions/${sessionId}`),
  archiveSession: (sessionId: string) =>
    apiFetch<{ ok: boolean }>(`/api/agent/sessions/${sessionId}`, {
      method: "DELETE",
    }),
  tools: () => apiFetch<{ tools: ToolSpec[] }>("/api/tools"),
  tasks: (page?: { limit: number; offset: number }) => apiFetch<Task[]>(
    page ? `/api/tasks?limit=${page.limit}&offset=${page.offset}` : "/api/tasks",
  ),
  task: (taskId: string, includeDetails = false) =>
    apiFetch<Task>(`/api/tasks/${taskId}?include_details=${includeDetails}`),
  retryTask: (taskId: string) =>
    apiFetch<Task>(`/api/tasks/${taskId}/retry`, {
      method: "POST",
      body: JSON.stringify({ client_retry_id: createClientId() }),
    }),
  createTask: (payload: { task_type: string; session_id?: string; input?: Record<string, unknown> }) =>
    apiFetch<Task>("/api/tasks", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  doctor: () => apiFetch<DoctorReport>("/api/doctor"),
  adminUsers: () => apiFetch<AdminUser[]>("/api/admin/users"),
  adminMetrics: () => apiFetch<AdminMetrics>("/api/admin/metrics"),
  updateAdminUser: (userId: string, payload: { disabled?: boolean; role?: string }) =>
    apiFetch<AdminUser>(`/api/admin/users/${userId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  revokeUserSessions: (userId: string) =>
    apiFetch<{ ok: boolean; revoked: number }>(`/api/admin/users/${userId}/revoke-sessions`, {
      method: "POST",
      body: JSON.stringify({}),
    }),
  ragQuery: (query: string, top_k = 5) =>
    apiFetch<{ backend: string; sources: Array<Record<string, unknown>> }>("/api/rag/query", {
      method: "POST",
      body: JSON.stringify({ query, top_k }),
    }),
};

export async function activeAgentTurn(sessionId: string, signal?: AbortSignal): Promise<ActiveAgentTurn | null> {
  const response = await fetch(`/api/agent/sessions/${sessionId}/turns/active`, {
    credentials: "include",
    signal,
  });
  if (response.status === 204) return null;
  if (!response.ok) return throwResponseError(response, "/api/agent");
  return response.json() as Promise<ActiveAgentTurn>;
}

export type AgentStreamResult = {
  status: "succeeded" | "failed";
  error?: string;
};

export function streamErrorMessage(event: StreamEvent): string {
  const detail = event.error;
  if (detail && typeof detail === "object" && "message" in detail) {
    return String(detail.message);
  }
  return typeof event.message === "string" ? event.message : "服务器执行失败，请稍后重试";
}

async function consumeAgentStream(
  response: Response,
  onEvent: (event: StreamEvent) => void,
): Promise<AgentStreamResult> {
  if (!response.body) throw new ApiError(0, "服务器没有返回可读取的消息流");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let completed = false;
  let status: AgentStreamResult["status"] | undefined;
  let error: string | undefined;
  const heartbeat = Number(response.headers.get("X-PSKit-SSE-Heartbeat-Seconds")) || 15;
  const idleTimeout = Math.max(45, heartbeat * 3) * 1000;

  const consumeFrame = (frame: string) => {
    const data = frame
      .split(/\r?\n/)
      .filter((item) => item.startsWith("data:"))
      .map((item) => item.replace(/^data:\s*/, ""))
      .join("\n");
    if (!data) return;
    let event: StreamEvent;
    try {
      event = JSON.parse(data) as StreamEvent;
    } catch {
      throw new ApiError(0, "服务器消息格式异常，请重新连接本会话");
    }
    if (event.type === "done") completed = true;
    if ((event.type === "message_done" || event.type === "done")
      && (event.status === "succeeded" || event.status === "failed")) {
      status = event.status;
    }
    if (event.type === "error") error = streamErrorMessage(event);
    onEvent(event);
  };

  try {
    while (!completed) {
      let timer: ReturnType<typeof setTimeout> | undefined;
      const next = reader.read();
      const stalled = new Promise<never>((_resolve, reject) => {
        timer = setTimeout(() => reject(new ApiError(0, "消息流长时间没有响应，正在恢复原任务")), idleTimeout);
      });
      const { value, done } = await Promise.race([next, stalled]).finally(() => clearTimeout(timer));
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split(/\r?\n\r?\n/);
      buffer = frames.pop() || "";
      for (const frame of frames) {
        consumeFrame(frame);
        if (completed) break;
      }
    }
    if (!completed) {
      buffer += decoder.decode();
      if (buffer.trim()) consumeFrame(buffer);
    }
    if (!completed) {
      throw new ApiError(0, "消息连接中断，正在从服务器恢复原任务");
    }
    const finalStatus = status || (error ? "failed" : "succeeded");
    return { status: finalStatus, error: finalStatus === "failed" ? error || "服务器执行失败，请稍后重试" : undefined };
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

export async function streamAgentMessage(
  sessionId: string,
  content: string,
  onEvent: (event: StreamEvent) => void,
  turnId = createClientId(),
  options: { signal?: AbortSignal; approvalId?: string } = {},
): Promise<AgentStreamResult> {
  const response = await fetch(`/api/agent/sessions/${sessionId}/message`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    signal: options.signal,
    body: JSON.stringify({ content, turn_id: turnId, ...(options.approvalId ? { approval_id: options.approvalId } : {}) }),
  });
  if (!response.ok) {
    return throwResponseError(response, "/api/agent");
  }
  return consumeAgentStream(response, onEvent);
}

export async function recoverAgentTurn(
  turnId: string,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<AgentStreamResult> {
  const response = await fetch(`/api/agent/turns/${turnId}/events`, {
    credentials: "include",
    signal,
  });
  if (!response.ok) return throwResponseError(response, "/api/agent");
  return consumeAgentStream(response, onEvent);
}
