import * as Dialog from "@radix-ui/react-dialog";
import { useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
import { useRef, useState } from "react";
import type { ResearchApi, Session } from "../../api/types";
import { useWorkspacePortalContainer } from "../../hooks/useWorkspacePortalContainer";
import { useLanguage } from "../../i18n/LanguageProvider";

export function SessionRenameDialog({ api, session, userId, onClose }: {
  api: ResearchApi;
  session: Session | null;
  userId: string;
  onClose: () => void;
}) {
  const { t } = useLanguage();
  const queryClient = useQueryClient();
  const portalContainer = useWorkspacePortalContainer();
  const [title, setTitle] = useState(session?.title ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const cleanTitle = title.trim();

  const rename = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!session || !cleanTitle || cleanTitle === session.title || busy) return;
    setBusy(true);
    setError(false);
    try {
      await api.renameSession(session.id, cleanTitle, session.project_id === `project-${userId}` ? null : session.project_id);
      await queryClient.invalidateQueries({ queryKey: ["sessions", userId, session.project_id] });
      onClose();
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  };

  return <Dialog.Root open={!!session} onOpenChange={(open) => { if (!open) onClose(); }}>
    <Dialog.Portal container={portalContainer}>
      <Dialog.Overlay className="mono-dialog-overlay" />
      <Dialog.Content className="mono-dialog-content" aria-describedby={undefined} onOpenAutoFocus={(event) => { event.preventDefault(); inputRef.current?.focus(); }}>
        <div className="mono-dialog-heading"><Dialog.Title>{t("mono.renameChat")}</Dialog.Title><Dialog.Close aria-label={t("project.close")}><X size={18} /></Dialog.Close></div>
        <form className="mono-form" onSubmit={(event) => void rename(event)}>
          <label>{t("mono.chatName")}<input ref={inputRef} value={title} onChange={(event) => setTitle(event.target.value)} maxLength={120} required /></label>
          {error && <p className="mono-form-error" role="alert">{t("mono.renameFailed")}</p>}
          <div className="mono-dialog-actions"><Dialog.Close className="mono-button" type="button">{t("project.cancel")}</Dialog.Close><button className="mono-button primary" type="submit" disabled={busy || !cleanTitle || cleanTitle === session?.title}>{t("mono.save")}</button></div>
        </form>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
