import { describe, expect, it } from "vitest";
import { ApiError } from "../../api/http";
import { getReconnectDecision } from "./reconnectPolicy";

describe("run reconnect policy", () => {
  it.each([401, 403, 404, 400, 409])("stops permanent HTTP %s failures", (status) => {
    expect(getReconnectDecision(new ApiError(status, "Cannot continue"), 0)).toEqual({ kind: "stop" });
  });

  it("stops aborts", () => {
    expect(getReconnectDecision(new DOMException("Cancelled", "AbortError"), 0)).toEqual({ kind: "stop" });
  });

  it.each([429, 503])("honors HTTP %s Retry-After without jitter or an early backoff cap", (status) => {
    expect(getReconnectDecision(new ApiError(status, "Please wait", "42"), 0, () => 0))
      .toEqual({ kind: "retry", delayMs: 42_000 });
    expect(getReconnectDecision(new ApiError(status, "Please wait", "0"), 0))
      .toEqual({ kind: "retry", delayMs: 0 });
  });

  it("stops if Retry-After exceeds a browser timer rather than retrying before the server deadline", () => {
    expect(getReconnectDecision(new ApiError(429, "Please wait", "2147484"), 0)).toEqual({ kind: "stop" });
  });

  it.each([new ApiError(503, "Unavailable"), new TypeError("Failed to fetch"), new Error("Connection closed")])(
    "backs off transient failures with jitter and a bounded delay", (error) => {
      expect(getReconnectDecision(error, 0, () => 0)).toEqual({ kind: "retry", delayMs: 800 });
      expect(getReconnectDecision(error, 0, () => 1)).toEqual({ kind: "retry", delayMs: 1_200 });
      expect(getReconnectDecision(error, 1, () => 0.5)).toEqual({ kind: "retry", delayMs: 2_000 });
      expect(getReconnectDecision(error, 5, () => 1)).toEqual({ kind: "retry", delayMs: 30_000 });
      expect(getReconnectDecision(error, 6)).toEqual({ kind: "stop" });
    },
  );
});
