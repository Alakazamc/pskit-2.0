import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { AuthSessionResponse, ResearchApi } from "../../api/types";
import { LanguageProvider } from "../../i18n/LanguageProvider";
import { GuestUpgrade } from "./GuestUpgrade";

afterEach(() => {
  cleanup();
  window.localStorage.removeItem("research_language");
  window.sessionStorage.clear();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

it("upgrades a guest through Python email verification while keeping the user identity", async () => {
  vi.stubEnv("VITE_TURNSTILE_SITE_KEY", "site-key");
  let challengeSolved: ((token: string) => void) | undefined;
  vi.stubGlobal("turnstile", {
    render: vi.fn((_container: HTMLElement, options: { callback: (token: string) => void }) => {
      challengeSolved = options.callback;
      return "upgrade-widget";
    }),
    reset: vi.fn(), remove: vi.fn(),
  });
  const session: AuthSessionResponse = {
    access_token: "member-jwt", expires_in: 3600,
    user: { id: "guest-1", email: "new@example.org", name: "New", is_anonymous: false },
  };
  const beginGuestEmailUpgrade = vi.fn().mockResolvedValue({ status: "check_email" });
  const verifyGuestEmailUpgrade = vi.fn().mockResolvedValue(session);
  const api = { beginGuestEmailUpgrade, verifyGuestEmailUpgrade } as unknown as ResearchApi;
  const onSession = vi.fn();
  render(<LanguageProvider><GuestUpgrade api={api} onSession={onSession} /></LanguageProvider>);
  const actor = userEvent.setup();

  expect(screen.getByText("只能升级为新账号；暂不合并已有账号。")).toBeInTheDocument();
  await actor.type(screen.getByLabelText("升级邮箱"), "new@example.org");
  challengeSolved?.("upgrade-proof");
  await actor.click(screen.getByRole("button", { name: "发送验证码" }));
  expect(beginGuestEmailUpgrade).toHaveBeenCalledWith("new@example.org", "upgrade-proof");
  await actor.type(screen.getByLabelText("邮件验证码"), "123456");
  await actor.click(screen.getByRole("button", { name: "验证并升级" }));
  expect(verifyGuestEmailUpgrade).toHaveBeenCalledWith("new@example.org", "123456");
  expect(onSession).toHaveBeenCalledWith(session);
});

it("offers the Python Google identity link without using a browser SDK", async () => {
  const beginGuestGoogleUpgrade = vi.fn().mockResolvedValue({ url: "https://accounts.google.com/oauth" });
  const api = { beginGuestGoogleUpgrade } as unknown as ResearchApi;
  render(<LanguageProvider><GuestUpgrade api={api} onSession={() => {}} /></LanguageProvider>);

  await userEvent.setup().click(screen.getByRole("button", { name: "关联 Google 账号" }));
  expect(beginGuestGoogleUpgrade).toHaveBeenCalledWith();
  expect(await screen.findByRole("link", { name: "继续前往 Google" })).toHaveAttribute(
    "href", "https://accounts.google.com/oauth",
  );
});

it("does not expose a Turnstile outage as an account-upgrade error", () => {
  vi.stubEnv("VITE_TURNSTILE_SITE_KEY", "site-key");
  let challengeFailed: (() => void) | undefined;
  vi.stubGlobal("turnstile", {
    render: vi.fn((_container: HTMLElement, options: { "error-callback": () => void }) => {
      challengeFailed = options["error-callback"];
      return "failed-widget";
    }),
    reset: vi.fn(), remove: vi.fn(),
  });
  render(<LanguageProvider><GuestUpgrade api={{} as ResearchApi} onSession={() => {}} /></LanguageProvider>);

  act(() => challengeFailed?.());

  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.queryByText("人机验证服务暂不可用，请稍后重试。")).not.toBeInTheDocument();
});

it("explains the new-account-only boundary in English", () => {
  window.localStorage.setItem("research_language", "en");
  render(<LanguageProvider><GuestUpgrade api={{} as ResearchApi} onSession={() => {}} /></LanguageProvider>);
  expect(screen.getByText(/Merging an existing account is not available yet/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Link Google account" })).toBeInTheDocument();
});
