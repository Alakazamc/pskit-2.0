import { lazy, Suspense } from "react";
import { getLoadedMarkdownContent, loadMarkdownContent } from "./markdownLoader";

const MarkdownContent = lazy(loadMarkdownContent);

export function LazyMarkdownContent({ text, streaming = false }: { text: string; streaming?: boolean }) {
  const LoadedMarkdownContent = getLoadedMarkdownContent();
  if (LoadedMarkdownContent) return <LoadedMarkdownContent text={text} streaming={streaming} />;
  return <Suspense fallback={<p className="message-text">{text}</p>}>
    <MarkdownContent text={text} streaming={streaming} />
  </Suspense>;
}
