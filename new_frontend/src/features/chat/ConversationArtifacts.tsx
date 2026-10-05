import * as Popover from "@radix-ui/react-popover";
import { useQuery } from "@tanstack/react-query";
import { Download, FileText, FolderOpen } from "lucide-react";
import { useState } from "react";
import type { ArtifactRef, ResearchApi } from "../../api/types";
import { DetailPanel } from "../../components/catalog/DetailPanel";
import { useWorkspacePortalContainer } from "../../hooks/useWorkspacePortalContainer";
import { useLanguage } from "../../i18n/LanguageProvider";

export function ConversationArtifacts({ api, userId, sessionId, sessionTitle, runActive }: {
  api: ResearchApi;
  userId: string;
  sessionId: string;
  sessionTitle: string;
  runActive: boolean;
}) {
  const { t } = useLanguage();
  const portalContainer = useWorkspacePortalContainer();
  const [open, setOpen] = useState(false);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const artifacts = useQuery({
    queryKey: ["session-artifacts", userId, sessionId],
    queryFn: () => api.getSessionArtifacts(sessionId),
    enabled: open,
    refetchInterval: open && runActive ? 3000 : false,
  });
  const selected = artifacts.data?.find((artifact) => artifact.id === previewId);
  const preview = useQuery({
    queryKey: ["artifact-preview", userId, previewId],
    queryFn: () => api.getArtifactPreview(previewId!),
    enabled: !!selected?.available,
  });

  const download = async (artifact: ArtifactRef) => {
    if (!artifact.available || busyId) return;
    setBusyId(artifact.id);
    setError("");
    try {
      const blob = await api.downloadArtifact(artifact.id);
      const url = URL.createObjectURL(blob);
      try {
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = artifact.name;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
      } finally {
        URL.revokeObjectURL(url);
      }
    } catch {
      setError(t("artifact.downloadFailed"));
    } finally {
      setBusyId(null);
    }
  };

  return <>
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild><button type="button" className="mono-header-action conversation-artifacts-trigger" aria-label={t("artifact.title")}>
        <FolderOpen size={17} aria-hidden="true" /><span>{t("artifact.title")}</span>
        {!!artifacts.data?.length && <small aria-hidden="true">{artifacts.data.length}</small>}
      </button></Popover.Trigger>
      <Popover.Portal container={portalContainer}><Popover.Content className="conversation-artifacts-popover" align="end" sideOffset={8} collisionPadding={12}>
        <div className="conversation-artifacts-session" title={sessionTitle}>{sessionTitle}</div>
        <div className="conversation-artifacts-heading"><FolderOpen size={16} aria-hidden="true" /><span>{t("artifact.outputs")}</span>
          {artifacts.isSuccess && <small>{artifacts.data.length}</small>}
        </div>
        {artifacts.isPending && <p role="status">{t("workspace.loadingCatalog")}</p>}
        {artifacts.isError && <p role="alert">{t("artifact.loadFailed")}</p>}
        {artifacts.isSuccess && artifacts.data.length === 0 && <p>{t("artifact.empty")}</p>}
        {artifacts.isSuccess && <div className="conversation-artifacts-list">{artifacts.data.map((artifact) =>
          <button type="button" key={artifact.id} onClick={() => { setOpen(false); setError(""); setPreviewId(artifact.id); }}>
            <FileText size={17} aria-hidden="true" /><span><strong>{artifact.name}</strong><small>{artifact.kind}</small></span>
          </button>
        )}</div>}
      </Popover.Content></Popover.Portal>
    </Popover.Root>
    <DetailPanel open={!!selected} onOpenChange={(nextOpen) => { if (!nextOpen) setPreviewId(null); }}
      title={selected?.name ?? ""} description={t("artifact.previewDescription")}>
      {selected && <>
        <button type="button" className="mono-button" disabled={!selected.available || busyId !== null}
          aria-label={t("artifact.downloadName", { name: selected.name })}
          onClick={() => void download(selected)}><Download size={16} />{selected.available ? t("artifact.download") : t("artifact.unavailable")}</button>
        {error && <p role="alert" className="mono-form-error">{error}</p>}
        {!selected.available ? <p className="mono-muted">{t("artifact.unavailable")}</p> : <>
          {preview.isPending && <p role="status">{t("artifact.previewLoading")}</p>}
          {preview.isError && <p role="alert" className="mono-form-error">{t("artifact.previewFailed")}</p>}
          {preview.data && <pre className="artifact-preview-text">{preview.data.text}</pre>}
        </>}
      </>}
    </DetailPanel>
  </>;
}
