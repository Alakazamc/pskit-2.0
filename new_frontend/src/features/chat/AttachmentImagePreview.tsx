import { useQuery } from "@tanstack/react-query";
import { Image } from "lucide-react";
import { useLayoutEffect, useState } from "react";

export function AttachmentImagePreview({ userId, fileId, loadImage, initialImage }: {
  userId: string;
  fileId: string;
  loadImage: (id: string) => Promise<Blob>;
  initialImage?: Blob;
}) {
  const image = useQuery({
    queryKey: ["attachment-image", userId, fileId],
    queryFn: () => loadImage(fileId),
    initialData: initialImage,
    staleTime: Infinity,
    gcTime: 5 * 60 * 1000,
    retry: false,
  });
  const [source, setSource] = useState<{ blob: Blob; url: string }>();
  const [failedUrl, setFailedUrl] = useState<string>();
  useLayoutEffect(() => {
    if (!image.data || typeof URL.createObjectURL !== "function") return;
    const url = URL.createObjectURL(image.data);
    setSource({ blob: image.data, url });
    return () => URL.revokeObjectURL(url);
  }, [image.data]);
  return source && source.blob === image.data && source.url !== failedUrl
    ? <img src={source.url} alt="" onError={() => setFailedUrl(source.url)} />
    : <div className="attachment-preview-glyph"><Image size={25} aria-hidden="true" /></div>;
}
