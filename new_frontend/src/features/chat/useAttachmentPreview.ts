import { useQuery } from "@tanstack/react-query";
import { useLayoutEffect, useState } from "react";

export type AttachmentPreviewSource = {
  userId: string;
  fileId: string;
  loadImage: (id: string) => Promise<Blob>;
  initialImage?: Blob;
};

export function useAttachmentPreview({ userId, fileId, loadImage, initialImage }: AttachmentPreviewSource) {
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
  const url = source && source.blob === image.data && source.url !== failedUrl ? source.url : undefined;
  return {
    url,
    loading: image.isPending || image.isFetching,
    unavailable: image.isError || (!url && !image.isPending),
    onError: () => setFailedUrl(source?.url),
    retry: () => { void image.refetch(); },
  };
}
