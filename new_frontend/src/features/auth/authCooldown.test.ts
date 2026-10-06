import { beforeEach, expect, it } from "vitest";
import { readAuthCooldown, writeAuthCooldown } from "./authCooldown";

beforeEach(() => window.sessionStorage.clear());

it("stores only an expiry under an action-scoped SHA-256 email key", async () => {
  await writeAuthCooldown("signup", "  User@Example.org ", 60, 1_000);

  expect(window.sessionStorage.length).toBe(1);
  const key = window.sessionStorage.key(0) ?? "";
  const value = window.sessionStorage.getItem(key) ?? "";
  expect(key).toMatch(/^pskit:auth-cooldown:v1:signup:[0-9a-f]{64}$/);
  expect(key).not.toContain("example.org");
  expect(value).toBe(JSON.stringify({ expiresAt: 61_000 }));
  expect(value).not.toContain("example.org");
  await expect(readAuthCooldown("signup", "user@example.org", 2_000)).resolves.toBe(59);
  await expect(readAuthCooldown("recovery", "user@example.org", 2_000)).resolves.toBe(0);
});

it("removes expired state and falls back when browser storage is unavailable", async () => {
  await writeAuthCooldown("recovery", "user@example.org", 1, 1_000);
  await expect(readAuthCooldown("recovery", "user@example.org", 2_001)).resolves.toBe(0);

  const broken = {
    getItem: () => { throw new Error("disabled"); },
    setItem: () => { throw new Error("disabled"); },
    removeItem: () => { throw new Error("disabled"); },
  } as unknown as Storage;
  await expect(writeAuthCooldown("signup", "user@example.org", 60, 1_000, broken))
    .resolves.toBeUndefined();
  await expect(readAuthCooldown("signup", "user@example.org", 1_000, broken)).resolves.toBe(0);
});
