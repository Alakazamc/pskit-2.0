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
  task: (taskId: string, includeDetails = false) =>
    apiFetch<Task>(`/api/tasks/${taskId}?include_details=${includeDetails}`),
  retryTask: (taskId: string) =>
    apiFetch<Task>(`/api/tasks/${taskId}/retry`, {
      method: "POST",
      body: JSON.stringify({ client_retry_id: crypto.randomUUID() }),
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

export async function activeAgentTurn(sessionId: string): Promise<ActiveAgentTurn | null> {
  const response = await fetch(`/api/agent/sessions/${sessionId}/turns/active`, {
    credentials: "include",
  });
  if (response.status === 204) return null;
  if (!response.ok) throw new ApiError(response.status, await parseError(response));
  return response.json() as Promise<ActiveAgentTurn>;
}

async function consumeAgentStream(
  response: Response,
  onEvent: (event: StreamEvent) => void,
): Promise<void> {
  if (!response.body) throw new ApiError(0, "服务器没有返回可读取的消息流");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let completed = false;

  const consumeFrame = (frame: string) => {
    const data = frame
      .split("\n")
      .filter((item) => item.startsWith("data:"))
      .map((item) => item.replace(/^data:\s*/, ""))
      .join("\n");
    if (!data) return;
    try {
      const event = JSON.parse(data) as StreamEvent;
      if (event.type === "done") completed = true;
      onEvent(event);
    } catch {
      onEvent({ type: "error", message: data });
    }
  };

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() || "";
    frames.forEach(consumeFrame);
  }
  buffer += decoder.decode();
  if (buffer.trim()) consumeFrame(buffer);
  if (!completed) {
    throw new ApiError(0, "消息连接中断，正在从服务器恢复原任务");
  }
}

export async function streamAgentMessage(
  sessionId: string,
  content: string,
  onEvent: (event: StreamEvent) => void,
  turnId = crypto.randomUUID(),
): Promise<void> {
  const response = await fetch(`/api/agent/sessions/${sessionId}/message`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content, turn_id: turnId }),
  });
  if (!response.ok) {
    throw new ApiError(response.status, await parseError(response));
  }
  await consumeAgentStream(response, onEvent);
}

export async function recoverAgentTurn(
  turnId: string,
  onEvent: (event: StreamEvent) => void,
): Promise<void> {
  const response = await fetch(`/api/agent/turns/${turnId}/events`, {
    credentials: "include",
  });
  if (!response.ok) throw new ApiError(response.status, await parseError(response));
  await consumeAgentStream(response, onEvent);
}
