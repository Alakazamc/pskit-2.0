import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "./App";

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });

function stubWorkspaceApi(artifactsBySession: Record<string, unknown[]> = {}) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(String(input), "http://test").pathname;
    const method = init?.method ?? "GET";
    if (path === "/api/v1/me") return json({ id: "alice", email: "alice@example.org", name: "Alice" });
    if (path === "/api/v1/g") return json([
      { id: "project-alice", name: "Personal", description: "" },
      { id: "project-lab", name: "Lab", description: "" },
    ]);
    if (path === "/api/v1/c" && method === "POST") return json({ id: "session-new", project_id: "project-alice", title: "Analyze P53" }, 201);
    if (path === "/api/v1/c") return json([{ id: "session-alice", project_id: "project-alice", title: "Personal research" }]);
    if (path === "/api/v1/g/g-p-lab/c") return json([{ id: "session-lab", project_id: "project-lab", title: "Lab research" }]);
    if (path === "/api/v1/g/g-p-lab/skills") return json({ skill_ids: [], default_skill_ids: [] });
    const artifactSession = path.match(/^\/api\/v1\/sessions\/([^/]+)\/artifacts$/);
    if (artifactSession) return json(artifactsBySession[artifactSession[1]] ?? []);
    if (path === "/api/v1/c/session-new/messages" && method === "POST") return json({ run_id: "run-new" });
    if (path.endsWith("/messages") || path === "/api/v1/skills" || path === "/api/v1/resources") return json([]);
    if (path === "/api/v1/files" || path === "/api/v1/artifacts") return json([]);
    if (path.includes("/events")) return new Response("", { status: 200 });
    return json({ detail: "Not found" }, 404);
  }));
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  window.history.replaceState(null, "", "/");
});

it("opens artifacts for the current chat from the upper-right control instead of the sidebar", async () => {
  window.localStorage.setItem("research_access_token", "demo-token");
  window.history.replaceState(null, "", "/session/session-alice");
  stubWorkspaceApi({ "session-alice": [{
    id: "artifact-one", name: "structure.cif", kind: "structure", available: true, size: 20,
  }] });
  render(<App />);

  expect(screen.queryByRole("link", { name: "产物" })).not.toBeInTheDocument();
  const actor = userEvent.setup();
  await actor.click(await screen.findByRole("button", { name: "产物" }));
  const panel = screen.getByRole("dialog");
  expect(within(panel).getByText("Personal research")).toBeInTheDocument();
  expect(within(panel).getByText("产出")).toBeInTheDocument();
  expect(within(panel).queryByText("来源")).not.toBeInTheDocument();
  expect(within(panel).queryByText("变更")).not.toBeInTheDocument();
  expect(await within(panel).findByText("structure.cif")).toBeInTheDocument();
  expect(fetch).toHaveBeenCalledWith(
    "/api/v1/sessions/session-alice/artifacts", expect.any(Object),
  );
});

it("opens a personal chat at /session and retains search and hash", async () => {
  window.localStorage.setItem("research_access_token", "demo-token");
  window.history.replaceState(null, "", "/session/session-alice?tab=messages#last");
  stubWorkspaceApi();
  render(<App />);
  await waitFor(() => expect(window.location.pathname).toBe("/session/session-alice"));
  expect(window.location.search).toBe("?tab=messages");
  expect(window.location.hash).toBe("#last");
  expect(await screen.findByRole("link", { name: "Personal research" })).toHaveAttribute("aria-current", "page");
});

it("opens a project chat at its /p/:projectId/c address", async () => {
  window.localStorage.setItem("research_access_token", "demo-token");
  window.history.replaceState(null, "", "/p/project-lab/c/session-lab");
  stubWorkspaceApi();
  render(<App />);
  expect(await screen.findByRole("link", { name: "Lab research" })).toHaveAttribute("aria-current", "page");
  expect(window.location.pathname).toBe("/p/project-lab/c/session-lab");
});

it("creates a personal conversation at /session using the /c API", async () => {
  window.localStorage.setItem("research_access_token", "demo-token");
  stubWorkspaceApi();
  render(<App />);
  const actor = userEvent.setup();
  await actor.type(await screen.findByLabelText("消息内容"), "Analyze P53");
  await actor.click(screen.getByRole("button", { name: "发送消息" }));
  await waitFor(() => expect(window.location.pathname).toBe("/session/session-new"));
  expect(fetch).toHaveBeenCalledWith("/api/v1/c", expect.objectContaining({ method: "POST" }));
});

it("redirects a bookmarked /c chat to /session without dropping search or hash", async () => {
  window.localStorage.setItem("research_access_token", "demo-token");
  window.history.replaceState(null, "", "/c/session-alice?tab=messages#last");
  stubWorkspaceApi();
  render(<App />);
  await waitFor(() => expect(window.location.pathname).toBe("/session/session-alice"));
  expect(window.location.search).toBe("?tab=messages");
  expect(window.location.hash).toBe("#last");
  expect(await screen.findByRole("link", { name: "Personal research" })).toHaveAttribute("aria-current", "page");
});

it("redirects a bookmarked project chat to its /p address", async () => {
  window.localStorage.setItem("research_access_token", "demo-token");
  window.history.replaceState(null, "", "/g/g-p-lab/c/session-lab?tab=plan#step-2");
  stubWorkspaceApi();
  render(<App />);
  await waitFor(() => expect(window.location.pathname).toBe("/p/project-lab/c/session-lab"));
  expect(window.location.search).toBe("?tab=plan");
  expect(window.location.hash).toBe("#step-2");
  expect(await screen.findByRole("link", { name: "Lab research" })).toHaveAttribute("aria-current", "page");
});
