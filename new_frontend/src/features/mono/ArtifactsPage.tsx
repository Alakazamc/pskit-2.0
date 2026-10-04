import { useQuery } from "@tanstack/react-query";
import { Download, FileText } from "lucide-react";
import { useState } from "react";
import type { ArtifactRef, ResearchApi } from "../../api/types";
import { useLanguage } from "../../i18n/LanguageProvider";
import { CatalogCard } from "../../components/catalog/CatalogCard";
import { DetailPanel } from "../../components/catalog/DetailPanel";

export function ArtifactsPage({ api, userId }: { api: ResearchApi; userId: string }) {
  const { t } = useLanguage();
  const artifacts = useQuery({ queryKey: ["artifacts", userId], queryFn: api.getArtifacts });
  const [busyId, setBusyId] = useState<string | null>(null);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const selected = (artifacts.data ?? []).find((artifact) => artifact.id === previewId);
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

  return <div className="mono-page-scroll"><div className="mono-page-content">
    {artifacts.isLoading && <p role="status">{t("workspace.loadingCatalog")}</p>}
    {artifacts.isError && <p className="mono-form-error" role="alert">{t("artifact.loadFailed")}</p>}
    <div className="catalog-entry-grid">{(artifacts.data ?? []).map((artifact) => <CatalogCard key={artifact.id}
      title={artifact.name} description={artifact.available ? `${artifact.kind}${artifact.size != null ? ` · ${artifact.size} B` : ""}` : t("artifact.unavailable")}
      icon={<FileText />} onOpen={() => { setError(""); setPreviewId(artifact.id); }} />)}</div>
    {!artifacts.isLoading && !artifacts.isError && artifacts.data?.length === 0 && <div className="mono-empty-panel"><FileText size={24} /><h2>{t("artifact.empty")}</h2><p>{t("artifact.emptyDescription")}</p></div>}
    <DetailPanel open={!!selected} onOpenChange={(open) => { if (!open) setPreviewId(null); }} title={selected?.name ?? ""} description={t("artifact.previewDescription")}>
      {selected && <>
        <button type="button" className="mono-button" disabled={!selected.available || busyId !== null}
          aria-label={t("artifact.downloadName", { name: selected.name })}
          onClick={() => void download(selected)}><Download size={16} />{selected.available ? t("artifact.download") : t("artifact.unavailable")}</button>
        {error && <p role="alert" className="mono-form-error">{error}</p>}
        {!selected.available ? <p className="mono-muted">{t("artifact.unavailable")}</p> : <>
          {preview.isLoading && <p role="status">{t("artifact.previewLoading")}</p>}
          {preview.isError && <p role="alert" className="mono-form-error">{t("artifact.previewFailed")}</p>}
          {preview.data && <pre className="artifact-preview-text">{preview.data.text}</pre>}
        </>}
      </>}
    </DetailPanel>
  </div></div>;
}
