import { Download, ExternalLink, FileArchive, FileCode2, FileImage, FileSpreadsheet, FileText, LoaderCircle } from "lucide-react";
import { useState } from "react";
import type { MessagePart } from "../../api/types";
import { DetailPanel } from "../../components/catalog/DetailPanel";
import { useLanguage } from "../../i18n/LanguageProvider";
import { ArtifactContentPreview } from "./ArtifactContentPreview";

export type ResourcePart = Extract<MessagePart, { type: "file" | "artifact" }>;

export type MessageResourceActions = {
  downloadFile?: (id: string) => Promise<Blob>;
  downloadArtifact?: (id: string) => Promise<Blob>;
  getArtifactPreview?: (id: string) => Promise<{ text: string }>;
};

const textPreviewExtensions = new Set(["csv", "fasta", "fa", "json", "md", "pdb", "txt", "yaml", "yml"]);

function extensionOf(name: string): string {
  const leaf = name.split(/[\\/]/).at(-1) ?? name;
  const index = leaf.lastIndexOf(".");
  return index > 0 && index < leaf.length - 1 ? leaf.slice(index + 1).toLowerCase() : "";
}

function formatLabel(part: ResourcePart): string {
  const extension = extensionOf(part.name);
  if (extension) return extension.toUpperCase();
  return part.type === "artifact" && part.kind ? part.kind : "FILE";
}

function ResourceGlyph({ name }: { name: string }) {
  const extension = extensionOf(name);
  if (["png", "jpg", "jpeg", "gif", "webp", "svg"].includes(extension)) return <FileImage size={21} aria-hidden="true" />;
  if (["csv", "tsv", "xls", "xlsx"].includes(extension)) return <FileSpreadsheet size={21} aria-hidden="true" />;
  if (["json", "pdb", "cif", "mmcif", "py", "r", "yaml", "yml"].includes(extension)) return <FileCode2 size={21} aria-hidden="true" />;
  if (["zip", "tar", "gz", "tgz"].includes(extension)) return <FileArchive size={21} aria-hidden="true" />;
  return <FileText size={21} aria-hidden="true" />;
}

function safeDownloadName(name: string): string {
  return Array.from(name.split(/[\\/]/).at(-1) || "download")
    .map((character) => {
      const code = character.charCodeAt(0);
      return code < 32 || code === 127 ? "_" : character;
    })
    .join("");
}

function saveBlob(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = safeDownloadName(name);
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
  }
}

function openBlob(blob: Blob): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.target = "_blank";
  anchor.rel = "noopener noreferrer";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

export function ResourceCard({ part, actions }: { part: ResourcePart; actions?: MessageResourceActions }) {
  const { t } = useLanguage();
  const [busy, setBusy] = useState<"open" | "download" | null>(null);
  const [error, setError] = useState("");
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewText, setPreviewText] = useState<string | null>(null);
  const loader = part.type === "file" ? actions?.downloadFile : actions?.downloadArtifact;
  const canTextPreview = part.type === "artifact"
    && textPreviewExtensions.has(extensionOf(part.name))
    && !!actions?.getArtifactPreview;

  const open = async () => {
    if (busy) return;
    setError("");
    if (canTextPreview) {
      setPreviewOpen(true);
      if (previewText !== null) return;
      setBusy("open");
      try {
        const preview = await actions!.getArtifactPreview!(part.id);
        setPreviewText(preview.text);
      } catch {
        setError(t("conversation.resourcePreviewFailed"));
      } finally {
        setBusy(null);
      }
      return;
    }
    if (!loader) return;
    setBusy("open");
    try { openBlob(await loader(part.id)); }
    catch { setError(t("conversation.resourceOpenFailed")); }
    finally { setBusy(null); }
  };

  const download = async () => {
    if (!loader || busy) return;
    setBusy("download");
    setError("");
    try { saveBlob(await loader(part.id), part.name); }
    catch { setError(t("conversation.resourceDownloadFailed")); }
    finally { setBusy(null); }
  };

  return <>
    <section className="message-resource-card" aria-label={part.name}>
      <div className="message-resource-icon"><ResourceGlyph name={part.name} /><span>{formatLabel(part)}</span></div>
      <div className="message-resource-copy"><strong title={part.name}>{part.name}</strong><span>{part.type === "artifact" ? part.kind : t("conversation.attachedFile")}</span></div>
      {loader && <div className="message-resource-actions">
        <button type="button" onClick={() => void open()} disabled={busy !== null} aria-label={t("conversation.openResource", { name: part.name })}>
          {busy === "open" ? <LoaderCircle className="message-spinner" size={15} aria-hidden="true" /> : <ExternalLink size={15} aria-hidden="true" />}
          <span>{t("conversation.open")}</span>
        </button>
        <button type="button" className="icon-only" onClick={() => void download()} disabled={busy !== null} aria-label={t("conversation.downloadResource", { name: part.name })} title={t("conversation.downloadResource", { name: part.name })}>
          {busy === "download" ? <LoaderCircle className="message-spinner" size={16} aria-hidden="true" /> : <Download size={16} aria-hidden="true" />}
        </button>
      </div>}
      {error && <p className="message-resource-error" role="alert">{error}</p>}
    </section>
    {canTextPreview && <DetailPanel open={previewOpen} onOpenChange={setPreviewOpen} title={part.name} description={t("conversation.resourcePreviewDescription")} className="message-resource-preview">
      {busy === "open" && previewText === null && <p role="status">{t("conversation.resourcePreviewLoading")}</p>}
      {previewText !== null && <ArtifactContentPreview name={part.name} text={previewText} />}
      {error && previewText === null && <p className="mono-form-error" role="alert">{error}</p>}
      {loader && <button type="button" className="mono-button" disabled={busy !== null} onClick={() => void download()}><Download size={16} aria-hidden="true" />{t("conversation.download")}</button>}
    </DetailPanel>}
  </>;
}

