import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Database, Sparkles, Upload } from "lucide-react";
import { useRef, useState } from "react";
import type { CatalogItem, ResearchApi } from "../../api/types";
import { useLanguage } from "../../i18n/LanguageProvider";
import { CatalogCard } from "./CatalogCard";
import { CatalogSearch } from "./CatalogSearch";
import { DetailPanel } from "./DetailPanel";

/** Public catalog metadata. Editing requires an owner-authorized write API. */
export function CatalogLibrary({ items, kind, loading, error, api }: {
  items: CatalogItem[]; kind: "skills" | "resources"; loading?: boolean; error?: boolean;
  api?: ResearchApi;
}) {
  const { t } = useLanguage();
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [visibility, setVisibility] = useState<"private" | "public">("private");
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState(false);
  const uploadInput = useRef<HTMLInputElement>(null);
  const queryClient = useQueryClient();
  const selected = items.find((item) => item.id === selectedId);
  const detail = useQuery({
    queryKey: ["skill-detail", selectedId],
    queryFn: () => api!.getSkill(selectedId!),
    enabled: kind === "skills" && !!api && !!selectedId,
  });
  const activeFile = detail.data?.files.find((file) => file.path === selectedFile)
    ?? detail.data?.files[0];
  const query = search.trim().toLocaleLowerCase();
  const filtered = items.filter((item) => `${item.name} ${item.description ?? ""}`.toLocaleLowerCase().includes(query));
  const upload = async (file?: File) => {
    if (!file || !api) return;
    setUploading(true); setUploadError(false);
    try {
      const created = await api.uploadSkill(file, visibility);
      await queryClient.invalidateQueries({ queryKey: ["skills"] });
      setSelectedId(created.id);
    } catch {
      setUploadError(true);
    } finally {
      setUploading(false);
      if (uploadInput.current) uploadInput.current.value = "";
    }
  };
  return <>
    <div className="catalog-library-toolbar"><CatalogSearch value={search} onChange={setSearch} label={t(kind === "skills" ? "catalog.searchSkills" : "catalog.searchResources")} />
      {kind === "skills" && api && <div className="skill-upload-controls">
        <select aria-label={t("catalog.skillVisibility")} value={visibility} onChange={(event) => setVisibility(event.target.value as "private" | "public")}>
          <option value="private">{t("catalog.privateSkill")}</option><option value="public">{t("catalog.requestPublic")}</option>
        </select>
        <input ref={uploadInput} type="file" accept=".zip,application/zip" hidden onChange={(event) => void upload(event.target.files?.[0])} />
        <button type="button" disabled={uploading} onClick={() => uploadInput.current?.click()}><Upload size={16} />{t(uploading ? "catalog.uploadingSkill" : "catalog.uploadSkill")}</button>
      </div>}
    </div>
    {uploadError && <p className="mono-form-error" role="alert">{t("catalog.skillUploadFailed")}</p>}
    {loading && <p role="status">{t("workspace.loadingCatalog")}</p>}
    {error && <p className="mono-form-error" role="alert">{t("workspace.catalogLoadFailed")}</p>}
    <div className="catalog-entry-grid">{filtered.map((item) => <CatalogCard key={item.id}
      title={item.name} description={item.description} icon={kind === "skills" ? <Sparkles /> : <Database />}
      meta={kind === "skills" ? t(item.visibility === "private" ? "catalog.privateSkill" : item.visibility === "review_pending" ? "catalog.pendingReview" : !item.available ? "catalog.dependencyUnavailable" : item.source === "upstream" ? "catalog.upstreamSkill" : "catalog.publicSkill") : undefined}
      onOpen={() => { setSelectedId(item.id); setSelectedFile(null); }} />)}</div>
    {!loading && !error && !filtered.length && <p className="mono-muted">{t(query ? "catalog.noMatches" : kind === "skills" ? "workspace.noSkills" : "workspace.noResources")}</p>}
    <DetailPanel open={!!selected} onOpenChange={(open) => { if (!open) setSelectedId(null); }} title={selected?.name ?? ""} description={selected?.description}>
      {selected && kind !== "skills" && <dl className="catalog-info"><dt>{t("catalog.description")}</dt><dd>{selected.description || "—"}</dd><dt>{t("catalog.identifier")}</dt><dd>{selected.id}</dd></dl>}
      {selected && kind === "skills" && <div className="skill-detail-layout">
        {detail.isPending ? <p role="status">{t("workspace.loadingCatalog")}</p> : detail.isError ? <p role="alert" className="mono-form-error">{t("workspace.catalogLoadFailed")}</p> : <>
          <aside className="skill-file-tree" aria-label={t("catalog.skillFiles")}>{detail.data?.files.map((file) => <button type="button" className={activeFile?.path === file.path ? "active" : ""} key={file.path} onClick={() => setSelectedFile(file.path)}>{file.path}</button>)}</aside>
          <section className="skill-file-content"><div className="skill-file-heading"><strong>{activeFile?.path ?? "—"}</strong><span>{activeFile ? `${activeFile.size} B` : ""}</span></div><pre>{activeFile?.content ?? ""}</pre></section>
        </>}
      </div>}
    </DetailPanel>
  </>;
}
