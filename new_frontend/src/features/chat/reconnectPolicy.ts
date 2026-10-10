import { ApiError } from "../../api/http";

export type ReconnectDecision = { kind: "stop" } | { kind: "retry"; delayMs: number };

export function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error && error.name === "AbortError";
}

export function getReconnectDecision(error: unknown, attempt: number, random = Math.random): ReconnectDecision {
  if (isAbortError(error)) return { kind: "stop" };
  // Bound consecutive reconnects as well as each exponential backoff delay.
  if (attempt >= 6) return { kind: "stop" };
  if (error instanceof ApiError) {
    if (error.status !== 408 && error.status !== 429 && error.status < 500) return { kind: "stop" };
    if (error.retryAfterSeconds !== undefined) {
      const delayMs = error.retryAfterSeconds * 1000;
      // Larger setTimeout values wrap to an immediate timer in browsers.
      return delayMs <= 2_147_483_647 ? { kind: "retry", delayMs } : { kind: "stop" };
    }
  }
  const baseMs = Math.min(30_000, 1_000 * 2 ** attempt);
  const jitter = 0.8 + Math.max(0, Math.min(1, random())) * 0.4;
  return { kind: "retry", delayMs: Math.min(30_000, Math.round(baseMs * jitter)) };
}
