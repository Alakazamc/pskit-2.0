import { useRef, useState } from "react";
import { Camera, Check, LoaderCircle } from "lucide-react";
import type { ResearchApi, UserIdentity } from "../../api/types";
import { ApiError } from "../../api/http";
import { UserAvatar } from "../../components/UserAvatar";
import { useLanguage } from "../../i18n/LanguageProvider";

export function ProfileSettings({ api, user, onUserChange }: {
  api: ResearchApi; user: UserIdentity; onUserChange: (user: UserIdentity) => void;
}) {
  const { t } = useLanguage();
  const [name, setName] = useState(user.name);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [saved, setSaved] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const perform = async (action: () => Promise<UserIdentity>) => {
    setBusy(true); setError(undefined); setSaved(false);
    try {
      onUserChange(await action());
      setSaved(true);
    } catch (caught) {
      const code = caught instanceof ApiError ? caught.code : undefined;
      setError(t(code === "AVATAR_TOO_LARGE" ? "profile.tooLarge"
        : code === "AVATAR_FORMAT_UNSUPPORTED" ? "profile.format"
        : code === "AVATAR_INVALID" ? "profile.invalid"
        : code === "AVATAR_STORAGE_UNAVAILABLE" ? "profile.storageUnavailable" : "profile.failed"));
    } finally { setBusy(false); }
  };
  const upload = (file?: File) => {
    if (!file) return;
    if (file.size > 4 * 1024 * 1024) { setError(t("profile.tooLarge")); setSaved(false); return; }
    if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) { setError(t("profile.format")); setSaved(false); return; }
    void perform(() => api.uploadAvatar(file));
  };
  return <section className="mono-panel mono-settings-panel profile-settings" aria-labelledby="profile-title">
    <h2 id="profile-title">{t("profile.title")}</h2>
    <div className="profile-avatar-row">
      <UserAvatar api={api} user={user} large />
      <div className="profile-avatar-actions">
        <button type="button" className="mono-button" disabled={busy} onClick={() => input.current?.click()}><Camera size={16} aria-hidden="true" />{t("profile.upload")}</button>
        <input className="profile-file-input" ref={input} type="file" accept="image/png,image/jpeg,image/webp" aria-label={t("profile.upload")} disabled={busy} onChange={(event) => { upload(event.target.files?.[0]); event.target.value = ""; }} />
        {user.avatar_revision && <button type="button" className="mono-text-button" disabled={busy} onClick={() => void perform(api.deleteAvatar)}>{t("profile.remove")}</button>}
      </div>
    </div>
    <form className="profile-name-form" onSubmit={(event) => {
      event.preventDefault(); const normalized = name.trim();
      if (!normalized || normalized.length > 80 || [...normalized].some((char) => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127)) { setError(t("profile.nameInvalid")); return; }
      void perform(async () => { const updated = await api.updateProfile(normalized); setName(updated.name); return updated; });
    }}>
      <label htmlFor="profile-name">{t("profile.name")}</label>
      <div className="profile-name-row"><input id="profile-name" value={name} maxLength={80} autoComplete="nickname" disabled={busy} onChange={(event) => { setName(event.target.value); setSaved(false); }} /><button className="mono-button" type="submit" disabled={busy || !name.trim() || name.trim() === user.name}>{t("profile.save")}</button></div>
    </form>
    {busy && <p className="profile-feedback" role="status"><LoaderCircle size={14} className="profile-loading" aria-hidden="true" />{t("profile.saving")}</p>}
    {error && <p className="profile-feedback profile-error" role="alert">{error}</p>}
    {saved && <p className="profile-feedback" role="status"><Check size={14} aria-hidden="true" />{t("profile.saved")}</p>}
  </section>;
}
