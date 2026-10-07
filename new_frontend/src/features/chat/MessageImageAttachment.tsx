import { Image, LoaderCircle } from "lucide-react";
import { useLanguage } from "../../i18n/LanguageProvider";
import { useAttachmentPreview, type AttachmentPreviewSource } from "./useAttachmentPreview";

export function MessageImageAttachment({ name, ...source }: AttachmentPreviewSource & { name: string }) {
  const { t } = useLanguage();
  const preview = useAttachmentPreview(source);
  return <figure className="user-message-image">
    {preview.url ? <img src={preview.url} alt={name} onError={preview.onError} />
      : <div className="user-message-image-fallback">
        {preview.loading ? <LoaderCircle className="message-spinner" size={20} aria-hidden="true" />
          : <Image size={24} aria-hidden="true" />}
        <span {...(preview.loading ? { role: "status", "aria-label": t("conversation.loadingImage", { name }) } : {})}>
          {t(preview.loading ? "conversation.loadingImage" : "conversation.imageUnavailable", { name })}
        </span>
        <small>{name}</small>
        {!preview.loading && <button type="button" onClick={preview.retry} aria-label={t("conversation.retryImage", { name })}>{t("conversation.retryImage", { name })}</button>}
      </div>}
  </figure>;
}
