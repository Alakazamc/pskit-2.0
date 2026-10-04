import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { ArrowUp, AtSign, CircleAlert, File, FilePlus2, FileText, Globe2, Plus, Sparkles, Square, X } from "lucide-react";
import { useDropzone } from "react-dropzone";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { CatalogItem, ContextRef, MessageRequest, ModelOption } from "../../api/types";
import { useComposerStore } from "./composerStore";
import { useLanguage } from "../../i18n/LanguageProvider";
import { errorTranslationKey } from "../../i18n/errors";
import type { TranslationKey } from "../../i18n/translations";
import { useWorkspacePortalContainer } from "../../hooks/useWorkspacePortalContainer";
import { ModelPicker } from "./ModelPicker";

type UploadTile = {
  key: string;
  name: string;
  kind: "image" | "pdf" | "html" | "file";
  previewUrl?: string;
  progress: number | null;
  status: "uploading" | "ready";
  refId?: string;
};

function fileKind(file: File | string): UploadTile["kind"] {
  const name = typeof file === "string" ? file : file.name;
  if (typeof file !== "string" && file.type.startsWith("image/")) return "image";
  if (/\.(png|jpe?g|gif|webp)$/i.test(name)) return "image";
  if (/\.pdf$/i.test(name)) return "pdf";
  if (/\.html?$/i.test(name)) return "html";
  return "file";
}

export function Composer({ onSend, onUpload, onStop, runActive = false, skills, projectSkillIds = [], resources, models = [], disabled = false }: { onSend: (message: MessageRequest) => Promise<boolean>; onUpload: (file: File, onProgress: (value: number) => void) => Promise<ContextRef>; onStop?: () => Promise<void>; runActive?: boolean; skills: CatalogItem[]; projectSkillIds?: string[]; resources: CatalogItem[]; models?: ModelOption[]; disabled?: boolean }) {
  const { t } = useLanguage();
  const [uploadTiles, setUploadTiles] = useState<UploadTile[]>([]);
  const [uploadError, setUploadError] = useState<TranslationKey | null>(null);
  const [stopError, setStopError] = useState<string | null>(null);
  const objectUrls = useRef(new Set<string>());
  const canceledUploads = useRef(new Set<string>());
  const portalContainer = useWorkspacePortalContainer();
  const content = useComposerStore((state) => state.content);
  const attachments = useComposerStore((state) => state.attachments);
  const selectedSkills = useComposerStore((state) => state.skills);
  const selectedResources = useComposerStore((state) => state.resources);
  const model = useComposerStore((state) => state.model);
  const reasoningEffort = useComposerStore((state) => state.reasoning_effort);
  const setModel = useComposerStore((state) => state.setModel);
  const setReasoningEffort = useComposerStore((state) => state.setReasoningEffort);
  const availableModels = Array.isArray(models) ? models : [];
  const selectedModel = model ? availableModels.find((option) => option.id === model) : availableModels[0];
  const modelUnavailable = Boolean(model && !selectedModel);
  const setText = useComposerStore((state) => state.setText);
  const addSkill = useComposerStore((state) => state.addSkill);
  const addResource = useComposerStore((state) => state.addResource);
  const addAttachment = useComposerStore((state) => state.addAttachment);
  const removeRef = useComposerStore((state) => state.removeRef);
  const clearAfterSend = useComposerStore((state) => state.clearAfterSend);
  const [stopping, setStopping] = useState(false);
  const picker = content.endsWith("/") ? "skill" : content.endsWith("@") ? "resource" : null;
  const projectSkillSet = new Set(projectSkillIds);
  const groupedSkills = [
    { label: t("composer.projectSkills"), items: skills.filter((skill) => projectSkillSet.has(skill.id)) },
    { label: t("composer.globalSkills"), items: skills.filter((skill) => !projectSkillSet.has(skill.id)) },
  ];
  useEffect(() => () => {
    objectUrls.current.forEach((url) => URL.revokeObjectURL(url));
    objectUrls.current.clear();
  }, []);
  const revokePreview = (tile: UploadTile) => {
    if (tile.previewUrl && objectUrls.current.delete(tile.previewUrl)) URL.revokeObjectURL(tile.previewUrl);
  };
  const removeUpload = (tile: UploadTile) => {
    if (tile.status === "uploading") canceledUploads.current.add(tile.key);
    if (tile.refId) removeRef("attachments", tile.refId);
    revokePreview(tile);
    setUploadTiles((current) => current.filter((item) => item.key !== tile.key));
  };
  const { getRootProps, getInputProps, open, isDragActive, inputRef } = useDropzone({
    noClick: true, noKeyboard: true,
    onDrop: async (files) => {
      setUploadError(null);
      if (files.some((file) => fileKind(file) === "image") && !selectedModel?.supports_images) {
        setUploadError("composer.imageRequiresVisionModel");
        if (inputRef.current) inputRef.current.value = "";
        return;
      }
      const currentImageCount = attachments.filter((item) => fileKind(item.name) === "image").length
        + uploadTiles.filter((item) => item.kind === "image" && item.status === "uploading").length;
      if (currentImageCount + files.filter((file) => fileKind(file) === "image").length > 2) {
        setUploadError("error.tooManyImages");
        if (inputRef.current) inputRef.current.value = "";
        return;
      }
      const entries = files.map((file): { file: File; tile: UploadTile } => {
        const previewUrl = fileKind(file) === "image" && typeof URL.createObjectURL === "function" ? URL.createObjectURL(file) : undefined;
        if (previewUrl) objectUrls.current.add(previewUrl);
        return { file, tile: { key: crypto.randomUUID(), name: file.name, kind: fileKind(file), previewUrl, progress: null, status: "uploading" } };
      });
      setUploadTiles((current) => [...current, ...entries.map(({ tile }) => tile)]);
      await Promise.all(entries.map(async ({ file, tile }) => {
        try {
          const ref = await onUpload(file, (progress) => setUploadTiles((current) => current.map((item) => item.key === tile.key ? { ...item, progress: Math.max(0, Math.min(100, progress)) } : item)));
          if (canceledUploads.current.delete(tile.key)) return;
          addAttachment(ref);
          setUploadTiles((current) => current.map((item) => item.key === tile.key ? { ...item, progress: 100, status: "ready", refId: ref.id } : item));
        } catch (caught) {
          if (!canceledUploads.current.delete(tile.key)) setUploadError(errorTranslationKey(caught) ?? "composer.uploadFailed");
          revokePreview(tile);
          setUploadTiles((current) => current.filter((item) => item.key !== tile.key));
        }
      }));
    },
  });
  const selectSkill = (ref: CatalogItem) => { addSkill(ref); setText(content.slice(0, -1)); };
  const selectResource = (ref: CatalogItem) => { addResource(ref); setText(content.slice(0, -1)); };
  const send = async () => {
    if (!content.trim() || disabled || runActive || modelUnavailable || uploadTiles.some((tile) => tile.status === "uploading")) return;
    if (attachments.some((item) => fileKind(item.name) === "image") && !selectedModel?.supports_images) {
      setUploadError("composer.imageRequiresVisionModel");
      return;
    }
    const refs = (items: ContextRef[]) => items.map(({ id, name }) => ({ id, name }));
    const effort = reasoningEffort && selectedModel?.reasoning_levels?.includes(reasoningEffort) ? reasoningEffort : undefined;
    if (await onSend({ content: content.trim(), model: selectedModel?.id, ...(effort ? { reasoning_effort: effort } : {}), attachments: refs(attachments), skills: refs(selectedSkills), resources: refs(selectedResources) })) {
      clearAfterSend();
      uploadTiles.forEach(revokePreview);
      setUploadTiles([]);
    }
  };
  const visibleUploads = [
    ...uploadTiles.filter((tile) => tile.status === "uploading" || attachments.some((ref) => ref.id === tile.refId)),
    ...attachments.filter((ref) => !uploadTiles.some((tile) => tile.refId === ref.id)).map((ref): UploadTile => ({ key: ref.id, refId: ref.id, name: ref.name, kind: fileKind(ref.name), progress: 100, status: "ready" })),
  ];
  const uploadErrorToast = uploadError && <div className="upload-error-toast" role="alert"><CircleAlert size={18} aria-hidden="true" /><span><strong>{t("composer.uploadFailed")}</strong>{uploadError !== "composer.uploadFailed" && <> · {t(uploadError)}</>}</span><button type="button" aria-label={t("composer.dismissUploadError")} onClick={() => setUploadError(null)}><X size={16} aria-hidden="true" /></button></div>;
  const toastHost = uploadErrorToast && typeof document !== "undefined" ? document.querySelector<HTMLElement>(".mono-main") : null;
  return <div className={`composer-wrap ${isDragActive ? "drag-active" : ""}`} {...getRootProps()}>
    <input {...getInputProps()} />
    {toastHost ? createPortal(uploadErrorToast, toastHost) : uploadErrorToast}
    <div className="composer-surface">
      {visibleUploads.length > 0 && <div className="attachment-previews">{visibleUploads.map((tile) => <div className={`attachment-preview ${tile.kind}`} key={tile.key} role="group" aria-label={t(tile.status === "uploading" ? tile.progress === null ? "composer.uploadingNamedUnknown" : "composer.uploadingNamed" : "composer.uploadedNamed", { name: tile.name, progress: tile.progress ?? 0 })}>
        {tile.previewUrl ? <img src={tile.previewUrl} alt="" /> : <div className="attachment-preview-glyph">{tile.kind === "pdf" ? <FileText size={25} /> : tile.kind === "html" ? <Globe2 size={25} /> : <File size={25} />}</div>}
        {tile.status === "uploading" && <div className={`attachment-preview-progress ${tile.progress === null ? "indeterminate" : ""}`} role="progressbar" aria-label={t(tile.progress === null ? "composer.uploadingNamedUnknown" : "composer.uploadingNamed", { name: tile.name, progress: tile.progress ?? 0 })} aria-valuemin={0} aria-valuemax={100} aria-valuenow={tile.progress ?? undefined}><svg viewBox="0 0 36 36" aria-hidden="true"><circle cx="18" cy="18" r="14" className="track" /><circle cx="18" cy="18" r="14" className="value" strokeDasharray={88} strokeDashoffset={tile.progress === null ? 66 : 88 * (1 - tile.progress / 100)} /></svg><span>{tile.progress === null ? "…" : `${tile.progress}%`}</span></div>}
        <div className="attachment-preview-name">{tile.kind === "pdf" && <span className="attachment-pdf-tag">PDF</span>}{tile.name}</div>
        <button type="button" className="attachment-preview-remove" aria-label={t("composer.remove", { name: tile.name })} onClick={() => removeUpload(tile)}><X size={13} /></button>
      </div>)}</div>}
      {(selectedSkills.length + selectedResources.length > 0) && <div className="composer-chips">
        {(["skills", "resources"] as const).flatMap((kind) => {
          const refs = kind === "skills" ? selectedSkills : selectedResources;
          return refs.map((ref) => <span className={`context-chip ${kind}`} key={`${kind}-${ref.id}`}><span>{kind === "skills" ? "✦" : kind === "resources" ? "@" : "▦"} {ref.name}</span><button type="button" aria-label={t("composer.remove", { name: ref.name })} onClick={() => removeRef(kind, ref.id)}><X size={13} /></button></span>);
        })}
      </div>}
      {attachments.length > 0 && <div className="upload-notice">{t("composer.uploadNotice")}</div>}
      <div className="composer-input-area">
        <textarea aria-label={t("composer.messageLabel")} value={content} onChange={(event) => setText(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void send(); } }} placeholder={t("composer.placeholder")} rows={3} />
        {picker && <div className="inline-picker" role="listbox">
          <div className="picker-heading">{t(picker === "skill" ? "composer.pickSkill" : "composer.addResource")}</div>
          {(picker === "skill" ? skills : resources).length === 0
            ? <div className="picker-empty">{t(picker === "skill" ? "composer.noSkills" : "composer.noResources")}</div>
            : picker === "skill" ? groupedSkills.filter((group) => group.items.length).map((group) => <div className="mono-picker-group" key={group.label}><div className="mono-picker-group-label">{group.label}</div>{group.items.map((ref) => <button key={ref.id} type="button" onClick={() => selectSkill(ref)}><Sparkles size={15} />{ref.name}</button>)}</div>)
              : resources.map((ref) => <button key={ref.id} type="button" onClick={() => selectResource(ref)}><AtSign size={15} />{ref.name}</button>)}
        </div>}
      </div>
      <div className="composer-toolbar">
        <DropdownMenu.Root>
          <DropdownMenu.Trigger asChild><button className="add-button" type="button" aria-label={t("composer.addContext")}><Plus size={19} /></button></DropdownMenu.Trigger>
          <DropdownMenu.Portal container={portalContainer}><DropdownMenu.Content className="add-menu" side="top" sideOffset={8} align="start">
            <DropdownMenu.Label>{t("composer.addContext")}</DropdownMenu.Label>
            <DropdownMenu.Item onSelect={() => open()}><FilePlus2 size={16} /> {t("composer.addFile")}</DropdownMenu.Item>
            <DropdownMenu.Separator />
            <DropdownMenu.Item onSelect={() => setText(`${content}/`)}><Sparkles size={16} /> {t("composer.skills")}</DropdownMenu.Item>
            <DropdownMenu.Item onSelect={() => setText(`${content}@`)}><AtSign size={16} /> {t("composer.resources")}</DropdownMenu.Item>
          </DropdownMenu.Content></DropdownMenu.Portal>
        </DropdownMenu.Root>
        <div className="composer-model-controls">
          <ModelPicker models={availableModels} selected={selectedModel} selectionUnavailable={modelUnavailable} effort={reasoningEffort} hasImages={attachments.some((item) => fileKind(item.name) === "image")} onModelChange={setModel} onEffortChange={setReasoningEffort} />
          {runActive && onStop ? <button type="button" className="send-button" aria-label={t("agent.cancel")} disabled={stopping} onClick={() => { setStopping(true); setStopError(null); void onStop().catch((caught) => { const key = errorTranslationKey(caught); setStopError(key ? t(key) : t("workspace.cancelFailed")); }).finally(() => setStopping(false)); }}><Square size={14} fill="currentColor" /></button>
            : <button type="button" className="send-button" aria-label={t("composer.send")} disabled={!content.trim() || disabled || runActive || modelUnavailable || uploadTiles.some((tile) => tile.status === "uploading")} onClick={() => void send()}><ArrowUp size={19} /></button>}
        </div>
      </div>
    </div>
    {stopError && <div className="composer-stop-error" role="alert">{stopError}</div>}
    <div className="composer-footer">{t("composer.disclaimer")}</div>
  </div>;
}
