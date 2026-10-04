import { vi } from "vitest";
import { configure } from "@testing-library/react";
import type { AdminJob, AdminMe, AdminModel, AdminService, AdminUser, AuditEvent, SandboxSummary } from "../../api/admin";

configure({ asyncUtilTimeout: 5000 });

export const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
export const administrator: AdminMe = { user_id: "alice", roles: ["platform_admin"], permissions: ["models:read", "models:write", "models:publish", "services:read", "services:write", "services:publish", "quotas:read", "quotas:write", "jobs:read", "jobs:cancel", "sandboxes:read", "sandboxes:drain", "usage:read", "usage:reconcile", "audit:read"], service_ids: [] };
export const modelRow: AdminModel = { id: "lab-chat", revision: 1, state: "draft", gateway_available: true, gateway: { id: "lab-chat", supports_images: false, reasoning_levels: [] }, draft: { expected_revision: 0, reason: "Reviewed initial policy", allowed_user_ids: ["alice"], allowed_group_ids: [], purposes: ["chat"], supports_images: false, reasoning_levels: [], default_for_purposes: [] }, published: null };
export const serviceRow: AdminService = { service_id: "rna", revision: 1, state: "draft", name: "RNA", owner_user_id: "alice", transport: "worker_pull", endpoint_ref: "lab/rna", credential_ref: "service/rna", model_version: "weights-v1", published_revision: null, schema_digest: null, capabilities: [{ id: "rna.predict", version: "1", input_schema: { type: "object" }, output_schema: { type: "object" }, required_usage: ["gpu_device_ms"], accepted_sources: ["service_reported"], visibility: "draft", allowed_users: ["alice"], gpu_count: 1, max_budget: { cpu_core_ms: 0, gpu_device_ms: 60000 }, concurrency: 1, max_execution_seconds: 1800, cancellation: "cooperative", limit_mode: "soft", exclusive_process: false }] };
export const userRow: AdminUser = { user_id: "alice", revision: 2, tier: "member", token_monthly_limit: 100000, gpu_daily_minutes: 60, cpu_daily_core_ms: 60000, concurrency_limit: 2, storage_limit_bytes: 1048576, tokens: { limit: 100000, used: 900, reserved: 100, remaining: 99000 }, gpu: { limit: 60, used: 12, reserved: 8, remaining: 40 }, cpu: { limit: 60000, used: 12000, reserved: 18000, remaining: 30000 } };
export const jobRow: AdminJob = { job_id: "compute-1", user_id: "alice", service_id: "rna", capability_id: "rna.predict", status: "running", accounting_status: "reserved", progress: 40, revision: 1, cancellation_state: "none" };
export const sandboxRow: SandboxSummary = { owner_id: "alice", instance_id: "container-1", volume_id: "volume-1", image_digest: "sha256:approved", state: "ready", runtime_state: "running", active_sessions: ["session-1"], revision: 1, last_completed_at: 1791043200 };
export const auditRow: AuditEvent = { event_id: "audit-1", actor_user_id: "alice", action: "quota.updated", resource_id: "bob", reason: "调整实验资源上限", request_id: "request-1", created_at: "2026-10-04T10:00:00Z", before: { gpu_daily_minutes: 60 }, after: { gpu_daily_minutes: 30 } };

export type AdminHttpHandler = (path: string, method: string, body: Record<string, unknown>, search: URLSearchParams) => Response | undefined | Promise<Response | undefined>;
export function setupAdmin(path: string, handler: AdminHttpHandler, me: AdminMe = administrator, language: "zh" | "en" = "zh") {
  window.localStorage.setItem("research_access_token", "user-jwt");
  window.localStorage.setItem("research_language", language);
  window.history.replaceState(null, "", path);
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://test");
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? JSON.parse(init.body) as Record<string, unknown> : {};
    if (url.pathname === "/api/v1/me") return json({ id: "alice", email: "alice@example.org", name: "Alice", is_anonymous: false });
    const response = await handler(url.pathname, method, body, url.searchParams);
    if (response) return response;
    if (url.pathname === "/api/v1/admin/me") return json(me);
    if (url.pathname.startsWith("/api/v1/admin/") && method === "GET") return json({ items: [], next_cursor: null });
    return json({ detail: { code: "NOT_FOUND" } }, 404);
  }));
}
