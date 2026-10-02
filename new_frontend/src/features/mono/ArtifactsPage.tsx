import * as Dialog from "@radix-ui/react-dialog";
import { useQuery } from "@tanstack/react-query";
import { Download, Eye, FileText, X } from "lucide-react";
import { useState } from "react";
import type { ArtifactRef, ResearchApi } from "../../api/types";
import { useLanguage } from "../../i18n/LanguageProvider";
import { useWorkspacePortalContainer } from "../../hooks/useWorkspacePortalContainer";

export function ArtifactsPage({ api, userId }: { api: ResearchApi; userId: string }) {
  const { t } = useLanguage();
  const portalContainer = useWorkspacePortalContainer();
  const artifacts = useQuery({ queryKey: ["artifacts", userId], queryFn: api.getArtifacts });
  const [busyId, setBusyId] = useState<string | null>(null);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const selected = (artifacts.data ?? []).find((artifact) => artifact.id === previewId);
  const preview = useQuery({
    queryKey: ["artifact-preview", userId, previewId],
    queryFn: () => api.getArtifactPreview(previewId!),
    enabled: !!previewId,
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

  return <div className="mono-page-scroll"><div className="mono-page-content">
    {artifacts.isError && <p className="mono-form-error" role="alert">{t("artifact.loadFailed")}</p>}
    {error && <p className="mono-form-error" role="alert">{error}</p>}
    <div className="mono-card-grid">{(artifacts.data ?? []).map((artifact) => <div className="mono-card" key={artifact.id}>
      <FileText size={21} /><h2>{artifact.name}</h2><p>{artifact.kind}{artifact.size != null ? ` · ${artifact.size} B` : ""}</p>
      <button type="button" className="mono-button" disabled={!artifact.available}
        aria-label={t("artifact.previewName", { name: artifact.name })}
        onClick={() => setPreviewId(artifact.id)}><Eye size={16} />{t("artifact.preview")}</button>
      <button type="button" className="mono-button" disabled={!artifact.available || busyId !== null}
        aria-label={t("artifact.downloadName", { name: artifact.name })}
        onClick={() => void download(artifact)}><Download size={16} />{artifact.available ? t("artifact.download") : t("artifact.unavailable")}</button>
    </div>)}</div>
    {!artifacts.isLoading && !artifacts.isError && artifacts.data?.length === 0 && <div className="mono-empty-panel"><FileText size={24} /><h2>{t("artifact.empty")}</h2><p>{t("artifact.emptyDescription")}</p></div>}
    <Dialog.Root open={!!selected} onOpenChange={(open) => { if (!open) setPreviewId(null); }}><Dialog.Portal container={portalContainer}>
      <Dialog.Overlay className="mono-dialog-overlay" />
      <Dialog.Content className="mono-dialog-content artifact-preview-dialog">
        <div className="mono-dialog-heading"><Dialog.Title>{selected?.name}</Dialog.Title><Dialog.Close aria-label={t("artifact.closePreview")}><X size={18} /></Dialog.Close></div>
        <Dialog.Description>{t("artifact.previewDescription")}</Dialog.Description>
        {preview.isLoading && <p>{t("artifact.previewLoading")}</p>}
        {preview.isError && <p role="alert" className="mono-form-error">{t("artifact.previewFailed")}</p>}
        {preview.data && <pre className="artifact-preview-text">{preview.data.text}</pre>}
      </Dialog.Content>
    </Dialog.Portal></Dialog.Root>
  </div></div>;
}
