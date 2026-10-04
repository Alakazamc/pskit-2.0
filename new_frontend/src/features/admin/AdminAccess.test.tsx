import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { App } from "../../app/App";
import { administrator, json as response, setupAdmin } from "./adminTestUtils";

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  window.history.replaceState(null, "", "/");
});

function adminSession(me: unknown, status = 200) {
  window.localStorage.setItem("research_access_token", "user-jwt");
  window.history.replaceState(null, "", "/admin/models");
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(String(input), "http://test").pathname;
    if (path === "/api/v1/me") return json({ id: "alice", email: "alice@example.org", name: "Alice", is_anonymous: false });
    if (path === "/api/v1/admin/me") {
      expect(init?.headers).toEqual(expect.objectContaining({ Authorization: "Bearer user-jwt" }));
      expect(init?.headers).not.toHaveProperty("X-Admin-Key");
      return json(me, status);
    }
    return json({ items: [], next_cursor: null });
  }));
}

it("denies an ordinary signed-in user access to management", async () => {
  adminSession({ detail: { code: "ADMIN_PERMISSION_DENIED" } }, 403);
  render(<App />);
  expect(await screen.findByRole("alert")).toHaveTextContent("没有管理权限");
  expect(screen.queryByRole("navigation", { name: "管理导航" })).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "返回工作台" })).toHaveAttribute("href", "/");
});

it("opens permitted management routes with the existing user token", async () => {
  adminSession({ user_id: "alice", roles: ["platform_admin"], permissions: ["models:read", "services:read", "quotas:read", "jobs:read", "sandboxes:read", "usage:read", "audit:read"], service_ids: [] });
  render(<App />);
  expect(await screen.findByRole("navigation", { name: "管理导航" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "语言模型" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "用户额度" })).toHaveAttribute("href", "/admin/users");
});

it("redirects an unauthenticated management visit to login", async () => {
  window.history.replaceState(null, "", "/admin/models");
  render(<App />);
  expect(await screen.findByRole("heading", { name: "登录" })).toBeInTheDocument();
  expect(window.location.pathname).toBe("/login");
});

it("offers an authorized management entry from the workspace and opens it by keyboard", async () => {
  setupAdmin("/settings", (path) => {
    if (path === "/api/v1/usage") return response({ storage: null, tokens: { limit: 100000, used: 0, reserved: 0, remaining: 100000, unit: "tokens", period: "month", resets_at: "2026-11-01T00:00:00Z" }, gpu: { limit: 60, used: 0, reserved: 0, remaining: 60, unit: "gpu_minutes", period: "day", resets_at: "2026-10-05T00:00:00Z" } });
    if (!path.startsWith("/api/v1/admin/") && path !== "/api/v1/me") return response([]);
  });
  render(<App />);
  const entry = await screen.findByRole("link", { name: "管理台" });
  entry.focus();
  await userEvent.setup().keyboard("{Enter}");
  expect(await screen.findByRole("heading", { name: "语言模型" })).toBeInTheDocument();
});

it("hides the workspace management entry for an ordinary user", async () => {
  setupAdmin("/settings", (path) => {
    if (path === "/api/v1/usage") return response({ storage: null, tokens: { limit: 100000, used: 0, reserved: 0, remaining: 100000, unit: "tokens", period: "month", resets_at: "2026-11-01T00:00:00Z" }, gpu: { limit: 60, used: 0, reserved: 0, remaining: 60, unit: "gpu_minutes", period: "day", resets_at: "2026-10-05T00:00:00Z" } });
    if (path.endsWith("/admin/me")) return response({ detail: { code: "ADMIN_FORBIDDEN" } }, 403);
    if (!path.startsWith("/api/v1/admin/") && path !== "/api/v1/me") return response([]);
  }, { ...administrator, roles: [], permissions: [] });
  render(<App />);
  await screen.findByRole("heading", { name: "账户" });
  expect(screen.queryByRole("link", { name: "管理台" })).not.toBeInTheDocument();
});
