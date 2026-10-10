import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "../../app/App";
import { useComposerStore } from "../chat/composerStore";

afterEach(() => {
  cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); localStorage.clear();
  useComposerStore.getState().clear(); window.history.replaceState(null, "", "/");
});

function setup(language = "zh") {
  const json = (value: unknown) => new Response(JSON.stringify(value), { status: 200 });
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/auth/csrf")) return json({ csrf_token: "signed-csrf" });
    if (path.endsWith("/auth/logout")) return new Response(null, { status: 204 });
    if (path.endsWith("/me")) return json({ id: "alice", name: "Alice", email: "alice@example.org", is_anonymous: false });
    if (path.endsWith("/g")) return json([{ id: "project-alice", name: "Personal", description: "" }]);
    if (path.endsWith("/usage")) return json({ tokens: { limit: 1000, remaining: 1000, used: 0, reserved: 0, resets_at: "2026-11-01T00:00:00Z" }, gpu: { limit: 60, remaining: 60, used: 0, reserved: 0, resets_at: "2026-10-06T00:00:00Z" } });
    if (path.includes("/usage/activity")) return json({ start_date: "2026-10-05", end_date: "2026-10-05", timezone: "UTC", days: [{ date: "2026-10-05", tokens: 0, gpu_ms: 0 }] });
    return json([]);
  });
  vi.stubGlobal("fetch", fetcher);
  localStorage.setItem("research_access_token", "demo-token");
  localStorage.setItem("research_language", language);
  window.history.replaceState(null, "", "/");
  return fetcher;
}

it("opens a compact account menu from the avatar row, removes the header setting entry and preserves the draft", async () => {
  setup(); const actor = userEvent.setup(); render(<App />);
  await actor.type(await screen.findByLabelText("消息内容"), "还没发送的消息");
  expect(screen.queryByRole("link", { name: "设置" })).not.toBeInTheDocument();
  const trigger = screen.getByRole("button", { name: "账号菜单：Alice" });
  await actor.click(trigger);
  const menu = screen.getByRole("menu", { name: "账号菜单" });
  expect(within(menu).getByText("alice@example.org")).toBeInTheDocument();
  expect(within(menu).getByRole("menuitem", { name: "个人资料" })).toHaveAttribute("href", "/settings#profile");
  expect(within(menu).getByRole("menuitem", { name: "设置" })).toHaveAttribute("href", "/settings");
  expect(within(menu).getByRole("menuitem", { name: "退出登录" })).toBeInTheDocument();
  await actor.keyboard("{Escape}");
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  await waitFor(() => expect(trigger).toHaveFocus());
  expect(screen.getByLabelText("消息内容")).toHaveValue("还没发送的消息");
});

it("opens profile editing and focuses the nickname, including a repeat visit on the same settings page", async () => {
  setup(); const actor = userEvent.setup(); render(<App />);
  await actor.click(await screen.findByRole("button", { name: "账号菜单：Alice" }));
  await actor.click(screen.getByRole("menuitem", { name: "个人资料" }));
  const nickname = await screen.findByRole("textbox", { name: "昵称" });
  expect(window.location.pathname + window.location.hash).toBe("/settings#profile");
  await waitFor(() => expect(nickname).toHaveFocus());
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  await actor.clear(nickname); await actor.type(nickname, "未保存的昵称");
  await actor.click(screen.getByRole("button", { name: "账号菜单：Alice" }));
  await actor.click(screen.getByRole("menuitem", { name: "个人资料" }));
  await waitFor(() => expect(nickname).toHaveFocus());
  expect(nickname).toHaveValue("未保存的昵称");
});

it("retains the avatar menu when the sidebar is collapsed and exposes translated settings", async () => {
  setup("en"); const actor = userEvent.setup(); render(<App />);
  await actor.click(await screen.findByRole("button", { name: "Collapse sidebar" }));
  const trigger = screen.getByRole("button", { name: "Account menu: Alice" });
  expect(trigger).toHaveClass("compact");
  await actor.click(trigger);
  expect(screen.getByRole("menu", { name: "Account menu" })).toBeInTheDocument();
  await actor.click(screen.getByRole("menuitem", { name: "Settings" }));
  expect(await screen.findByRole("textbox", { name: "Nickname" })).toHaveValue("Alice");
  expect(window.location.pathname).toBe("/settings");
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
});

it("logs out through the avatar menu using the existing server logout contract", async () => {
  const fetcher = setup(); const actor = userEvent.setup(); render(<App />);
  await actor.click(await screen.findByRole("button", { name: "账号菜单：Alice" }));
  await actor.click(screen.getByRole("menuitem", { name: "退出登录" }));
  await waitFor(() => expect(localStorage.getItem("research_access_token")).toBeNull());
  expect(fetcher.mock.calls.some(([input]) => String(input).endsWith("/auth/logout"))).toBe(true);
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
});
