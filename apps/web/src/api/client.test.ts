import { afterEach, expect, it, vi } from "vitest";
import { createApi } from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

it("uses the HTTP contract for projects instead of browser-owned fixtures", async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify([
    { id: "project-from-api", name: "来自接口的项目", description: "" },
  ]), { status: 200, headers: { "content-type": "application/json" } }));
  vi.stubGlobal("fetch", fetcher);
  window.localStorage.setItem("research_access_token", "test-token");

  const projects = await createApi().getProjects();

  expect(projects[0].name).toBe("来自接口的项目");
  expect(fetcher).toHaveBeenCalledWith("/api/v1/g", expect.objectContaining({
    headers: expect.objectContaining({ Authorization: "Bearer test-token" }),
  }));
});
