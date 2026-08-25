import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiFetch } from "./api";


afterEach(() => {
  vi.unstubAllGlobals();
});

describe("apiFetch", () => {
  it("includes credentials and decodes JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiFetch<{ ok: boolean }>("/api/health")).resolves.toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/health",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("surfaces API error details", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "denied" }), {
          status: 403,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    await expect(apiFetch("/api/private")).rejects.toEqual(new ApiError(403, "denied"));
  });
});
