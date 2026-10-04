import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "../../app/App";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); localStorage.clear(); window.history.replaceState(null, "", "/"); });
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });

function setup(failSave = false) {
  let user = { id: "alice", name: "Alice", email: "alice@example.org", is_anonymous: false, avatar_revision: null as string | null };
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/me/avatar")) {
      if (init?.method === "PUT") { user = { ...user, avatar_revision: "a".repeat(32) }; return json(user); }
      if (init?.method === "DELETE") { user = { ...user, avatar_revision: null }; return json(user); }
      return new Response(new Blob(["avatar"], { type: "image/webp" }));
    }
    if (path.endsWith("/me")) {
      if (init?.method === "PATCH") {
        if (failSave) return json({ detail: { code: "PROFILE_UNAVAILABLE" } }, 503);
        user = { ...user, ...JSON.parse(String(init.body)) }; return json(user);
      }
      return json(user);
    }
    if (path.includes("/usage/activity")) return json({ start_date: "2026-10-01", end_date: "2026-10-05", timezone: "UTC", days: [0, 20, 100, 0, 300].map((tokens, index) => ({ date: `2026-10-0${index + 1}`, tokens, gpu_ms: index === 4 ? 120000 : 0 })) });
    if (path.endsWith("/usage")) return json({ tokens: { limit: 1000, remaining: 580, used: 420, reserved: 0, resets_at: "2026-11-01T00:00:00Z" }, gpu: { limit: 60, remaining: 58, used: 2, reserved: 0, resets_at: "2026-10-06T00:00:00Z" } });
    if (path.endsWith("/g")) return json([{ id: "project-alice", name: "Personal", description: "" }]);
    if (path.endsWith("/c") || path.endsWith("/skills") || path.endsWith("/resources") || path.endsWith("/usage/entries") || path.endsWith("/models")) return json([]);
    return json({ detail: "Not found" }, 404);
  });
  localStorage.setItem("research_access_token", "demo-token");
  window.history.replaceState(null, "", "/settings");
  vi.stubGlobal("fetch", fetcher);
  return { fetcher, currentUser: () => user };
}

it("saves a nickname through Python, updates the sidebar and restores it after reload", async () => {
  const { fetcher } = setup();
  const actor = userEvent.setup();
  const view = render(<App />);
  const nickname = await screen.findByLabelText("昵称");
  await actor.clear(nickname); await actor.type(nickname, "小林");
  await actor.click(screen.getByRole("button", { name: "保存昵称" }));
  await waitFor(() => expect(document.querySelector(".mono-sidebar-footer b")).toHaveTextContent("小林"));
  expect(fetcher.mock.calls.some(([path, init]) => String(path).endsWith("/me") && init?.method === "PATCH" && JSON.parse(String(init.body)).name === "小林")).toBe(true);
  view.unmount(); render(<App />);
  expect(await screen.findByLabelText("昵称")).toHaveValue("小林");
});

it("retains a rejected nickname and does not overwrite the saved sidebar name", async () => {
  setup(true); const actor = userEvent.setup(); render(<App />);
  const nickname = await screen.findByLabelText("昵称");
  await actor.clear(nickname); await actor.type(nickname, "待保存");
  await actor.click(screen.getByRole("button", { name: "保存昵称" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("保存失败");
  expect(nickname).toHaveValue("待保存");
  expect(document.querySelector(".mono-sidebar-footer b")).toHaveTextContent("Alice");
});

it("uploads avatars without exposing credentials and removes them through the same owner API", async () => {
  const { fetcher } = setup(); const actor = userEvent.setup();
  vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:avatar");
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
  render(<App />);
  const file = new File(["picture"], "face.png", { type: "image/png" });
  await actor.upload(await screen.findByLabelText("上传头像"), file);
  expect(await screen.findByRole("button", { name: "移除头像" })).toBeInTheDocument();
  await waitFor(() => expect(document.querySelector(".mono-sidebar-footer img")).toHaveAttribute("src", "blob:avatar"));
  const upload = fetcher.mock.calls.find(([path, init]) => String(path).endsWith("/me/avatar") && init?.method === "PUT");
  expect(upload?.[1]?.body).toBe(file);
  expect(upload?.[1]?.headers).toEqual(expect.objectContaining({ Authorization: "Bearer demo-token", "Content-Type": "image/png" }));
  await actor.click(screen.getByRole("button", { name: "移除头像" }));
  await waitFor(() => expect(document.querySelector(".mono-sidebar-footer img")).toBeNull());
});

it("renders server daily usage as keyboard-accessible squares and switches Token/GPU metrics", async () => {
  setup(); const actor = userEvent.setup(); render(<App />);
  const cell = await screen.findByRole("button", { name: "2026-10-05：300 Token" });
  expect(document.querySelectorAll(".usage-activity-cell[data-date]")).toHaveLength(5);
  await actor.click(cell);
  expect(screen.getByTestId("usage-activity-details")).toHaveTextContent("300 Token");
  await actor.click(screen.getByRole("button", { name: "GPU 活动" }));
  const gpu = screen.getByRole("button", { name: "2026-10-05：2 GPU 分钟" });
  expect(gpu).toHaveAttribute("data-level", "4");
  gpu.focus(); await actor.keyboard("{ArrowUp}");
  expect(screen.getByRole("button", { name: "2026-10-04：0 GPU 分钟" })).toHaveFocus();
});
