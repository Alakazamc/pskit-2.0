import { Image } from "lucide-react";
import { useAttachmentPreview, type AttachmentPreviewSource } from "./useAttachmentPreview";

export function AttachmentImagePreview(props: AttachmentPreviewSource) {
  const preview = useAttachmentPreview(props);
  return preview.url
    ? <img src={preview.url} alt="" onError={preview.onError} />
    : <div className="attachment-preview-glyph"><Image size={25} aria-hidden="true" /></div>;
}
