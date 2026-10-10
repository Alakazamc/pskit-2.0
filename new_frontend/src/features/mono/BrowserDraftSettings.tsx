import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { Session } from "../../api/types";
import { useLanguage } from "../../i18n/LanguageProvider";
import { clearComposerDraft, clearUserComposerDrafts, readBrowserDraftPolicy, storedDraftScopes, writeBrowserDraftPolicy, type BrowserDraftPolicy } from "../chat/composerDrafts";

export function BrowserDraftSettings({ userId }: { userId: string }) {
  const { t } = useLanguage();
  const queryClient = useQueryClient();
  const [policy, setPolicy] = useState(() => readBrowserDraftPolicy(userId));
  const [scopes, setScopes] = useState(() => storedDraftScopes(userId));
  const [selected, setSelected] = useState("");
  const [notice, setNotice] = useState<"cleared" | "unavailable" | null>(null);
  const update = (patch: Partial<BrowserDraftPolicy>) => {
    const next = { ...policy, ...patch };
    if (writeBrowserDraftPolicy(userId, next)) { setPolicy(next); setNotice(null); }
    else setNotice("unavailable");
  };
  const cachedSessions = queryClient.getQueriesData<Session[]>({ queryKey: ["sessions", userId] }).flatMap(([, sessions]) => sessions ?? []);
  const label = (id: string) => {
    if (id === "new:personal") return t("drafts.newPersonal");
    if (id.startsWith("new:project:")) return t("drafts.newProject");
    const sessionId = id.startsWith("session:") ? id.slice(8) : id;
    const cached = cachedSessions.find((session) => session.id === sessionId)
      ?? queryClient.getQueryData<Session>(["session", userId, sessionId]);
    return cached?.title || t("drafts.conversation", { id: sessionId.slice(0, 12) });
  };
  const refresh = () => { setScopes(storedDraftScopes(userId)); setSelected(""); setNotice("cleared"); };
  return <section className="mono-panel mono-settings-panel browser-draft-settings">
    <h2>{t("drafts.title")}</h2>
    <p className="settings-description">{t("drafts.description")}</p>
    <label className="settings-check"><input type="checkbox" checked={policy.enabled} onChange={(event) => update({ enabled: event.target.checked })} />{t("drafts.retain")}</label>
    <p className="settings-description">{t("drafts.retention")}</p>
    <label className="settings-check"><input type="checkbox" checked={policy.clearOnSignOut} onChange={(event) => update({ clearOnSignOut: event.target.checked })} />{t("drafts.clearOnSignOut")}</label>
    <div className="settings-draft-actions">
      <label className="settings-draft-selection">{t("drafts.selectConversation")}<select value={selected} onChange={(event) => setSelected(event.target.value)}>
        <option value="">{t("drafts.chooseConversation")}</option>
        {scopes.map((scope) => <option key={scope.conversationId} value={scope.conversationId}>{label(scope.conversationId)}</option>)}
      </select></label>
      <button type="button" className="mono-button" disabled={!selected} onClick={() => { clearComposerDraft({ userId, conversationId: selected }); refresh(); }}>{t("drafts.clearConversation")}</button>
      <button type="button" className="mono-button" disabled={!scopes.length} onClick={() => { clearUserComposerDrafts(userId); refresh(); }}>{t("drafts.clearAll")}</button>
    </div>
    {notice && <p role="status" className="settings-description">{t(notice === "cleared" ? "drafts.cleared" : "drafts.unavailable")}</p>}
  </section>;
}
