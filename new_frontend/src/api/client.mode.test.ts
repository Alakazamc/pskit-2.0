import { afterEach, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

it("does not silently choose demo authentication in a production build", async () => {
  vi.stubEnv("PROD", true);
  vi.stubEnv("DEV", false);
  vi.stubEnv("VITE_AUTH_MODE", undefined);
  vi.resetModules();

  const { isDemoAuth } = await import("./client");
  expect(isDemoAuth).toBe(false);
});
