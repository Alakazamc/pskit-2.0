import type { AuthSessionResponse, ResearchApi, UserIdentity } from "../../api/types";
import { LanguageSwitch, useLanguage } from "../../i18n/LanguageProvider";
import { GuestUpgrade } from "../auth/GuestUpgrade";
import { AdminEntry } from "../admin/AdminEntry";
import { ProfileSettings } from "./ProfileSettings";
import { UsageSettings } from "./UsageSettings";

export function SettingsPage({ api, user, theme, onThemeChange, onUserChange, onSession, onLogout }: {
  api: ResearchApi; user: UserIdentity; theme: "dark" | "light";
  onThemeChange: (theme: "dark" | "light") => void; onUserChange: (user: UserIdentity) => void;
  onSession?: (session: AuthSessionResponse) => void; onLogout: () => void;
}) {
  const { t } = useLanguage();
  return <div className="mono-page-scroll"><div className="mono-page-content settings-content">
    <ProfileSettings key={user.id} api={api} user={user} onUserChange={onUserChange} />
    <div className="settings-preferences">
      <section className="mono-panel mono-settings-panel"><h2>{t("mono.appearance")}</h2><div className="mono-segmented"><button className={theme === "light" ? "active" : ""} onClick={() => onThemeChange("light")}>{t("mono.light")}</button><button className={theme === "dark" ? "active" : ""} onClick={() => onThemeChange("dark")}>{t("mono.dark")}</button></div></section>
      <section className="mono-panel mono-settings-panel"><h2>{t("mono.language")}</h2><LanguageSwitch /></section>
    </div>
    <UsageSettings api={api} userId={user.id} isGuest={user.is_anonymous} />
    {user.is_anonymous && onSession && <GuestUpgrade api={api} onSession={onSession} />}
    <section className="mono-panel mono-settings-panel"><h2>{t("mono.account")}</h2><p className="settings-account-email">{user.email || t("profile.guest")}</p><AdminEntry api={api} userId={user.id} /><button className="mono-button" onClick={onLogout}>{t("mono.signOut")}</button></section>
  </div></div>;
}
