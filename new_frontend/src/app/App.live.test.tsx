import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "./App";

const auth = vi.hoisted(() => ({
  refreshAuth: vi.fn(), setAccessToken: vi.fn(),
}));

vi.mock("../api/client", () => ({
  isDemoAuth: false,
  createApi: () => ({
    refreshAuth: auth.refreshAuth,
    setAccessToken: auth.setAccessToken,
    getProjects: async () => [], getUsage: async () => ({
      tokens: { limit: 100, used: 0, reserved: 0, remaining: 100, unit: "tokens", period: "month", resets_at: "2026-11-01T00:00:00Z" },
      gpu: { limit: 60, used: 0, reserved: 0, remaining: 60, unit: "gpu_minutes", period: "day", resets_at: "2026-10-02T00:00:00Z" },
    }),
    getSkills: async () => [], getResources: async () => [], getFiles: async () => [], getArtifacts: async () => [],
  }),
}));

afterEach(() => {
  cleanup();
  auth.refreshAuth.mockReset(); auth.setAccessToken.mockReset();
  window.history.replaceState(null, "", "/");
});

it("restores a Python-managed Google login from its refresh cookie", async () => {
  auth.refreshAuth.mockResolvedValue({ access_token: "fresh-jwt", expires_in: 3600, user: { id: "alice", email: "alice@example.org", name: "Alice" } });
  window.history.replaceState(null, "", "/auth/callback");
  render(<App />);

  expect(await screen.findByText("Alice", {}, { timeout: 3000 })).toBeInTheDocument();
  expect(auth.refreshAuth).toHaveBeenCalledOnce();
  expect(auth.setAccessToken).toHaveBeenCalledWith("fresh-jwt");
  expect(window.location.pathname).toBe("/");
});
