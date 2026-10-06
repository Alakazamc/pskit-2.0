import { useEffect, useState } from "react";
import { ApiError } from "../../api/http";
import type { AuthSessionResponse, ResearchApi } from "../../api/types";
import { useLanguage } from "../../i18n/LanguageProvider";
import { errorTranslationKey } from "../../i18n/errors";
import type { TranslationKey } from "../../i18n/translations";
import { AuthCaptcha } from "./AuthCaptcha";
import { readAuthCooldown, writeAuthCooldown } from "./authCooldown";

export function GuestUpgrade({ api, onSession }: {
  api: ResearchApi;
  onSession: (session: AuthSessionResponse) => void;
}) {
  const { t } = useLanguage();
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [googleUrl, setGoogleUrl] = useState<string | null>(null);
  const [error, setError] = useState<TranslationKey | null>(null);
  const [captchaToken, setCaptchaToken] = useState<string | null>(null);
  const [captchaReset, setCaptchaReset] = useState(0);
  const [cooldown, setCooldown] = useState(0);
  const captchaSiteKey = import.meta.env.VITE_TURNSTILE_SITE_KEY ?? "";

  useEffect(() => {
    let active = true;
    const refresh = async () => {
      const seconds = await readAuthCooldown("guest-upgrade", email);
      if (active) setCooldown(seconds);
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 1000);
    return () => { active = false; window.clearInterval(timer); };
  }, [email]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (!sent) {
        await api.beginGuestEmailUpgrade(email.trim(), captchaToken ?? undefined);
        await writeAuthCooldown("guest-upgrade", email, 60);
        setCooldown(60);
        setSent(true);
      } else {
        onSession(await api.verifyGuestEmailUpgrade(email.trim(), code.trim()));
      }
    } catch (caught) {
      if (!sent && caught instanceof ApiError && caught.retryAfterSeconds) {
        setCooldown(caught.retryAfterSeconds);
        await writeAuthCooldown("guest-upgrade", email, caught.retryAfterSeconds);
      }
      setError(errorTranslationKey(caught) ?? "guest.upgradeFailed");
    } finally {
      setBusy(false);
      if (!sent && captchaSiteKey) {
        setCaptchaToken(null);
        setCaptchaReset((count) => count + 1);
      }
    }
  };
  const beginGoogle = async () => {
    setBusy(true);
    setError(null);
    try {
      const { url } = await api.beginGuestGoogleUpgrade();
      setGoogleUrl(url);
    } catch (caught) {
      setError(errorTranslationKey(caught) ?? "guest.upgradeFailed");
    } finally { setBusy(false); }
  };

  return <section className="mono-panel mono-settings-panel guest-upgrade" aria-labelledby="guest-upgrade-title">
    <h2 id="guest-upgrade-title">{t("guest.upgradeTitle")}</h2>
    <p>{t("guest.newAccountOnly")}</p>
    <form onSubmit={(event) => void submit(event)}>
      <label htmlFor="guest-upgrade-email">{t("guest.email")}</label>
      <input id="guest-upgrade-email" type="email" autoComplete="email" value={email}
        onChange={(event) => setEmail(event.target.value)} disabled={sent || busy} required />
      {sent && <><label htmlFor="guest-upgrade-code">{t("auth.emailCode")}</label>
        <input id="guest-upgrade-code" inputMode="numeric" autoComplete="one-time-code"
          value={code} onChange={(event) => setCode(event.target.value)} required /></>}
      {!sent && captchaSiteKey && <AuthCaptcha siteKey={captchaSiteKey}
        action="guest_upgrade_email" resetSignal={captchaReset} onToken={setCaptchaToken} />}
      {error && <p role="alert">{t(error)}</p>}
      <button type="submit" className="mono-button"
        disabled={busy || (!sent && (cooldown > 0 || Boolean(captchaSiteKey && !captchaToken)))}>
        {sent ? t("guest.verifyEmail") : cooldown > 0
          ? t("auth.retryIn", { seconds: cooldown }) : t("guest.sendCode")}
      </button>
    </form>
    <button type="button" className="mono-button" disabled={busy} onClick={() => void beginGoogle()}>{t("guest.linkGoogle")}</button>
    {googleUrl && <a className="mono-button" href={googleUrl} referrerPolicy="no-referrer">{t("guest.continueGoogle")}</a>}
  </section>;
}
