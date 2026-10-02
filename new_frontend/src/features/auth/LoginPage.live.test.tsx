import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { ResearchApi } from "../../api/types";
import { LanguageProvider } from "../../i18n/LanguageProvider";
import { LoginPage } from "./LoginPage";
import { ApiError } from "../../api/http";

vi.mock("../../api/client", () => ({ isDemoAuth: false }));

afterEach(() => { cleanup(); window.localStorage.removeItem("research_language"); vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

it("uses Python login and Google endpoints without calling Supabase from the browser", async () => {
  const loginEmail = vi.fn().mockResolvedValue({
    access_token: "server-issued-jwt", expires_in: 3600,
    user: { id: "alice", email: "alice@example.org", name: "Alice" },
  });
  const api = { loginEmail, googleLoginUrl: () => "/api/v1/auth/google/start" } as unknown as ResearchApi;
  const onLogin = vi.fn();
  const user = userEvent.setup();
  window.localStorage.clear();
  render(<LoginPage api={api} onLogin={onLogin} />);

  await user.type(screen.getByLabelText("邮箱"), "alice@example.org");
  await user.type(screen.getByLabelText("密码"), "secret");
  await user.click(screen.getByRole("button", { name: /邮箱登录/ }));

  expect(loginEmail).toHaveBeenCalledWith("alice@example.org", "secret");
  expect(onLogin).toHaveBeenCalledWith({ id: "alice", email: "alice@example.org", name: "Alice" }, "server-issued-jwt");
  expect(window.localStorage.getItem("research_access_token")).toBeNull();
  expect(screen.getByRole("link", { name: "使用 Google 登录" })).toHaveAttribute("href", "/api/v1/auth/google/start");
});

it("starts a guest session only after the visitor chooses to try it", async () => {
  const session = { access_token: "guest-jwt", expires_in: 3600,
    user: { id: "guest-1", email: "", name: "Guest", is_anonymous: true } };
  const startAnonymous = vi.fn().mockResolvedValue(session);
  const api = { startAnonymous, googleLoginUrl: () => "/api/v1/auth/google/start" } as unknown as ResearchApi;
  const onLogin = vi.fn();
  render(<LanguageProvider><LoginPage api={api} onLogin={onLogin} /></LanguageProvider>);

  expect(startAnonymous).not.toHaveBeenCalled();
  await userEvent.setup().click(screen.getByRole("button", { name: "先以游客身份体验" }));
  expect(startAnonymous).toHaveBeenCalledWith();
  expect(onLogin).toHaveBeenCalledWith(session.user, "guest-jwt");
  expect(window.localStorage.getItem("research_access_token")).toBeNull();
});

it("requires a fresh Turnstile token before live guest signup and sends it only to Python", async () => {
  vi.stubEnv("VITE_TURNSTILE_SITE_KEY", "site-key");
  let challengeSolved: ((token: string) => void) | undefined;
  const reset = vi.fn();
  const renderWidget = vi.fn((_container: HTMLElement, options: { callback: (token: string) => void }) => {
    challengeSolved = options.callback;
    return "widget-1";
  });
  vi.stubGlobal("turnstile", { render: renderWidget, reset, remove: vi.fn() });
  const startAnonymous = vi.fn().mockRejectedValue(new ApiError(503, "Unavailable"));
  const api = { startAnonymous, googleLoginUrl: () => "/api/v1/auth/google/start" } as unknown as ResearchApi;
  render(<LanguageProvider><LoginPage api={api} onLogin={() => {}} /></LanguageProvider>);

  const guestButton = screen.getByRole("button", { name: "先以游客身份体验" });
  expect(guestButton).toBeDisabled();
  expect(renderWidget).toHaveBeenCalledWith(expect.any(HTMLElement), expect.objectContaining({ sitekey: "site-key" }));
  challengeSolved?.("challenge-token");
  await userEvent.setup().click(guestButton);
  expect(startAnonymous).toHaveBeenCalledWith("challenge-token");
  expect(reset).toHaveBeenCalledWith("widget-1");
  expect(guestButton).toBeDisabled();
});

it("explains when live guest access is disabled by the Python server", async () => {
  const api = { startAnonymous: vi.fn().mockRejectedValue(new ApiError(404, "Unavailable")),
    googleLoginUrl: () => "/api/v1/auth/google/start" } as unknown as ResearchApi;
  render(<LanguageProvider><LoginPage api={api} onLogin={() => {}} /></LanguageProvider>);
  await userEvent.setup().click(screen.getByRole("button", { name: "先以游客身份体验" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("游客体验当前未开放。");
});

it("lets guests switch the login page to English", async () => {
  window.localStorage.removeItem("research_language");
  const api = { googleLoginUrl: () => "/api/v1/auth/google/start" } as unknown as ResearchApi;
  render(<LanguageProvider><LoginPage api={api} onLogin={() => {}} /></LanguageProvider>);
  await userEvent.setup().click(screen.getByRole("button", { name: "English" }));
  expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  expect(screen.getByLabelText("Email")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Continue with Google" })).toBeInTheDocument();
});

it("uses an explicit theme control and saves the login appearance", async () => {
  window.localStorage.clear();
  const api = { googleLoginUrl: () => "/api/v1/auth/google/start" } as unknown as ResearchApi;
  render(<LanguageProvider><LoginPage api={api} onLogin={() => {}} /></LanguageProvider>);
  const page = screen.getByRole("main");
  expect(page).toHaveClass("dark");
  await userEvent.setup().click(screen.getByRole("button", { name: "切换到浅色模式" }));
  expect(page).toHaveClass("light");
  expect(window.localStorage.getItem("pskit-theme")).toBe("light");
  await userEvent.setup().click(screen.getByRole("button", { name: "切换到深色模式" }));
  expect(page).toHaveClass("dark");
});

it("signs up and verifies an email code through Python", async () => {
  const signupEmail = vi.fn().mockResolvedValue({ status: "check_email", session: null });
  const verifyEmailCode = vi.fn().mockResolvedValue({
    access_token: "verified-jwt", expires_in: 3600,
    user: { id: "alice", email: "alice@example.org", name: "Alice" },
  });
  const api = { signupEmail, verifyEmailCode, googleLoginUrl: () => "/api/v1/auth/google/start" } as unknown as ResearchApi;
  const onLogin = vi.fn();
  render(<LanguageProvider><LoginPage api={api} onLogin={onLogin} /></LanguageProvider>);
  const user = userEvent.setup();

  await user.click(screen.getByRole("button", { name: "注册账号" }));
  await user.type(screen.getByLabelText("邮箱"), "alice@example.org");
  await user.type(screen.getByLabelText("密码"), "strong-password");
  await user.click(screen.getByRole("button", { name: "发送验证邮件" }));
  expect(signupEmail).toHaveBeenCalledWith("alice@example.org", "strong-password");
  await user.type(screen.getByLabelText("邮件验证码"), "123456");
  await user.click(screen.getByRole("button", { name: "验证邮箱" }));
  expect(verifyEmailCode).toHaveBeenCalledWith("alice@example.org", "123456", "signup");
  expect(onLogin).toHaveBeenCalledWith({ id: "alice", email: "alice@example.org", name: "Alice" }, "verified-jwt");
});

it("resets a password after verifying a recovery code", async () => {
  const requestPasswordRecovery = vi.fn().mockResolvedValue({ status: "email_sent" });
  const verifyEmailCode = vi.fn().mockResolvedValue({
    access_token: "recovery-jwt", expires_in: 3600,
    user: { id: "alice", email: "alice@example.org", name: "Alice" },
  });
  const updatePassword = vi.fn().mockResolvedValue(undefined);
  const api = { requestPasswordRecovery, verifyEmailCode, updatePassword,
    googleLoginUrl: () => "/api/v1/auth/google/start" } as unknown as ResearchApi;
  const onLogin = vi.fn();
  render(<LanguageProvider><LoginPage api={api} onLogin={onLogin} /></LanguageProvider>);
  const user = userEvent.setup();

  await user.click(screen.getByRole("button", { name: "忘记密码" }));
  await user.type(screen.getByLabelText("邮箱"), "alice@example.org");
  await user.click(screen.getByRole("button", { name: "发送重置邮件" }));
  expect(requestPasswordRecovery).toHaveBeenCalledWith("alice@example.org");
  await user.type(screen.getByLabelText("邮件验证码"), "654321");
  await user.click(screen.getByRole("button", { name: "验证邮箱" }));
  await user.type(screen.getByLabelText("新密码"), "new-strong-password");
  await user.click(screen.getByRole("button", { name: "更新密码" }));
  expect(verifyEmailCode).toHaveBeenCalledWith("alice@example.org", "654321", "recovery");
  expect(updatePassword).toHaveBeenCalledWith("recovery-jwt", "new-strong-password");
  expect(onLogin).toHaveBeenCalledWith({ id: "alice", email: "alice@example.org", name: "Alice" }, "recovery-jwt");
});
