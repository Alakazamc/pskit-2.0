import { useEffect, useState } from "react";
import { ArrowRight, Globe2, Moon, Sun } from "lucide-react";
import { isDemoAuth } from "../../api/client";
import { ApiError } from "../../api/http";
import type { AuthSessionResponse, ResearchApi, UserIdentity } from "../../api/types";
import { useLanguage } from "../../i18n/LanguageProvider";
import { errorTranslationKey } from "../../i18n/errors";
import type { TranslationKey } from "../../i18n/translations";
import moleculeArtwork from "../../assets/login-molecules.png";
import { GuestCaptcha } from "./GuestCaptcha";

export function LoginPage({ api, onLogin }: { api: ResearchApi; onLogin: (user: UserIdentity, token: string) => void }) {
  const { language, setLanguage, t } = useLanguage();
  const [theme, setTheme] = useState<"dark" | "light">(() => {
    try { return window.localStorage.getItem("pskit-theme") === "light" ? "light" : "dark"; }
    catch { return "dark"; }
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { window.localStorage.setItem("pskit-theme", theme); } catch { /* Storage may be disabled. */ }
  }, [theme]);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [mode, setMode] = useState<"login" | "signup" | "recover" | "verify" | "newPassword">("login");
  const [verificationKind, setVerificationKind] = useState<"signup" | "recovery">("signup");
  const [recoverySession, setRecoverySession] = useState<AuthSessionResponse | null>(null);
  const [error, setError] = useState<TranslationKey | null>(null);
  const [busy, setBusy] = useState(false);
  const guestCaptchaSiteKey = isDemoAuth ? "" : (import.meta.env.VITE_TURNSTILE_SITE_KEY ?? "");
  const [guestCaptchaToken, setGuestCaptchaToken] = useState<string | null>(null);
  const [guestCaptchaReset, setGuestCaptchaReset] = useState(0);
  const startGuest = async () => {
    setBusy(true); setError(null);
    try {
      const session = guestCaptchaSiteKey
        ? await api.startAnonymous(guestCaptchaToken ?? undefined)
        : await api.startAnonymous();
      if (isDemoAuth) window.localStorage.setItem("research_access_token", session.access_token);
      onLogin(session.user, session.access_token);
    } catch (caught) {
      setError(caught instanceof ApiError && caught.status === 404
        ? "guest.unavailable" : errorTranslationKey(caught) ?? "auth.failed");
    } finally {
      setBusy(false);
      if (guestCaptchaSiteKey) {
        setGuestCaptchaToken(null);
        setGuestCaptchaReset((count) => count + 1);
      }
    }
  };
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true); setError(null);
    try {
      if (isDemoAuth || mode === "login") {
        const result = isDemoAuth ? await api.loginDemo(email) : await api.loginEmail(email, password);
        if (isDemoAuth) window.localStorage.setItem("research_access_token", result.access_token);
        onLogin(result.user, result.access_token);
      } else if (mode === "signup") {
        const result = await api.signupEmail(email, password);
        if (result.session) onLogin(result.session.user, result.session.access_token);
        else { setVerificationKind("signup"); setMode("verify"); setPassword(""); }
      } else if (mode === "recover") {
        await api.requestPasswordRecovery(email);
        setVerificationKind("recovery"); setMode("verify");
      } else if (mode === "verify") {
        const result = await api.verifyEmailCode(email, code, verificationKind);
        if (verificationKind === "signup") onLogin(result.user, result.access_token);
        else { setRecoverySession(result); setMode("newPassword"); setPassword(""); }
      } else if (recoverySession) {
        await api.updatePassword(recoverySession.access_token, password);
        onLogin(recoverySession.user, recoverySession.access_token);
      }
    } catch (caught) {
      const key = caught instanceof ApiError && caught.status === 429
        ? "error.authRateLimited" : caught instanceof ApiError && caught.status === 401
          ? mode === "verify" ? "error.invalidOtp" : "error.invalidCredentials"
          : errorTranslationKey(caught);
      setError(key ?? "auth.failed");
    }
    finally { setBusy(false); }
  };
  const submitLabel = isDemoAuth ? "auth.demoEnter" : mode === "login" ? "auth.emailLogin"
    : mode === "signup" ? "auth.sendSignup" : mode === "recover" ? "auth.sendRecovery"
      : mode === "verify" ? "auth.verifyEmail" : "auth.updatePassword";
  return <main className={`login-page ${theme}`}>
    <section className="login-hero" aria-label="PSKit Research">
      <div className="login-brand"><div className="brand-mark" aria-hidden="true"><svg viewBox="0 0 32 32"><path d="M16 4c1.5 7.1 4.9 10.5 12 12-7.1 1.5-10.5 4.9-12 12C14.5 20.9 11.1 17.5 4 16 11.1 14.5 14.5 11.1 16 4Z" /></svg></div><span>PSKit Research</span></div>
      <img className="login-artwork" src={moleculeArtwork} alt="" aria-hidden="true" />
      <div className="login-story"><span className="login-eyebrow">{t("auth.companion")}</span><h1>{t("auth.heroFirst")}<br />{t("auth.heroBridge")}<em>{t("auth.heroHighlight")}</em></h1><p>{t("auth.heroDescription")}</p></div>
      <div className="login-mantra" aria-hidden="true">{language === "en" ? <>FASTER IDEAS.<br />DEEPER INSIGHTS.<br />REAL PROGRESS.</> : <>更快的想法。<br />更深的洞见。<br />真实的进展。</>}</div>
    </section>
    <section className="login-auth" aria-label={t("auth.welcome")}>
      <div className="login-header-controls"><button type="button" className="login-theme-toggle" aria-label={t(theme === "dark" ? "theme.switchToLight" : "theme.switchToDark")} title={t(theme === "dark" ? "theme.switchToLight" : "theme.switchToDark")} onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>{theme === "dark" ? <Sun size={19} aria-hidden="true" /> : <Moon size={19} aria-hidden="true" />}</button><div className="login-language-switch" role="group" aria-label={t("language.label")}><Globe2 size={18} aria-hidden="true" /><button type="button" aria-label="English" aria-pressed={language === "en"} onClick={() => setLanguage("en")}>EN</button><button type="button" aria-label="简体中文" aria-pressed={language === "zh"} onClick={() => setLanguage("zh")}>中文</button></div></div>
      <div className="login-card"><h2>{t(mode === "login" ? "auth.welcome" : mode === "signup" ? "auth.signupTitle" : mode === "recover" ? "auth.recoveryTitle" : mode === "verify" ? "auth.verifyTitle" : "auth.newPasswordTitle")}</h2><p className="login-subtitle">{t(mode === "verify" ? "auth.checkEmail" : "auth.subtitle")}</p>
        <form onSubmit={submit}>
          {mode !== "newPassword" && <div className="login-field"><label htmlFor="login-email">{t("auth.email")}</label><input id="login-email" type="email" autoComplete="email" placeholder="you@example.org" value={email} onChange={(event) => setEmail(event.target.value)} required /></div>}
          {(isDemoAuth && mode === "login" || !isDemoAuth && ["login", "signup", "newPassword"].includes(mode)) && <div className="login-field"><label htmlFor="login-password">{t(mode === "newPassword" ? "auth.newPassword" : "auth.password")}</label><div className="login-password-field"><input id="login-password" type="password" autoComplete={mode === "newPassword" || mode === "signup" ? "new-password" : "current-password"} minLength={mode === "login" ? 1 : 8} value={password} onChange={(event) => setPassword(event.target.value)} required={!isDemoAuth} disabled={isDemoAuth} placeholder="••••••••" />{!isDemoAuth && mode === "login" && <button type="button" className="login-forgot" onClick={() => { setMode("recover"); setError(null); }}>{t("auth.forgotPassword")}</button>}</div></div>}
          {mode === "verify" && <div className="login-field"><label htmlFor="login-code">{t("auth.emailCode")}</label><input id="login-code" inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(event) => setCode(event.target.value)} required /></div>}
          {error && <div className="form-error" role="alert">{t(error)}</div>}
          <button className="login-submit" type="submit" disabled={busy}>{t(submitLabel)} <ArrowRight size={17} aria-hidden="true" /></button>
        </form>
        {!isDemoAuth && mode === "login" && <><div className="login-divider"><span>{language === "en" ? "or" : "或"}</span></div><a className="google-button" href={api.googleLoginUrl()}><span className="google-g" aria-hidden="true">G</span>{t("auth.googleLogin")}</a><div className="auth-mode-actions"><span>{language === "en" ? "Don't have an account?" : "还没有账号？"}</span><button type="button" onClick={() => { setMode("signup"); setError(null); }}>{t("auth.signUp")}</button></div></>}
        {!isDemoAuth && mode !== "login" && <button type="button" className="auth-back-button" onClick={() => { setMode("login"); setError(null); }}>{t("auth.backToLogin")}</button>}
        {mode === "login" && <>
          {guestCaptchaSiteKey && <GuestCaptcha siteKey={guestCaptchaSiteKey} resetSignal={guestCaptchaReset} onToken={setGuestCaptchaToken} />}
          <button type="button" className="google-button guest-entry-button" disabled={busy || Boolean(guestCaptchaSiteKey && !guestCaptchaToken)} onClick={() => void startGuest()}>{t("auth.tryGuest")}</button>
        </>}
        {isDemoAuth && <div className="login-footnote">{t("auth.demoNote")}</div>}
      </div>
    </section>
  </main>;
}
