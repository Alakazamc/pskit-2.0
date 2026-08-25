export type User = {
  id: string;
  username: string;
  role: "admin" | "user" | string;
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
  task_type: string;
  status: string;
  progress: number;
  error_type: string | null;
  error_message: string | null;
  input: Record<string, unknown> | null;
  output: Record<string, unknown> | null;
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

async function parseError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body.detail === "string") return body.detail;
    return JSON.stringify(body);
  } catch {
    return response.statusText || "Request failed";
  }
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
    throw new ApiError(response.status, await parseError(response));
  }
  return response.json() as Promise<T>;
}

export const api = {
  me: () => apiFetch<User>("/api/auth/me"),
  login: (username: string, password: string) =>
    apiFetch<User>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  register: (username: string, password: string) =>
    apiFetch<User>("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({ username, password }),
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
  tasks: () => apiFetch<Task[]>("/api/tasks"),
  createTask: (payload: { task_type: string; session_id?: string; input?: Record<string, unknown> }) =>
    apiFetch<Task>("/api/tasks", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  doctor: () => apiFetch<DoctorReport>("/api/doctor"),
  ragQuery: (query: string, top_k = 5) =>
    apiFetch<{ backend: string; sources: Array<Record<string, unknown>> }>("/api/rag/query", {
      method: "POST",
      body: JSON.stringify({ query, top_k }),
    }),
};

export async function streamAgentMessage(
  sessionId: string,
  content: string,
  onEvent: (event: StreamEvent) => void,
): Promise<void> {
  const response = await fetch(`/api/agent/sessions/${sessionId}/message`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!response.ok) {
    throw new ApiError(response.status, await parseError(response));
  }
  if (!response.body) return;

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() || "";
    for (const frame of frames) {
      const line = frame
        .split("\n")
        .find((item) => item.startsWith("data:"));
      if (!line) continue;
      const raw = line.replace(/^data:\s*/, "");
      try {
        onEvent(JSON.parse(raw) as StreamEvent);
      } catch {
        onEvent({ type: "error", message: raw });
      }
    }
  }
}
