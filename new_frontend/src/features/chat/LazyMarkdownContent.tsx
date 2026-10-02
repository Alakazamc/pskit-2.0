import { lazy, Suspense } from "react";

const MarkdownContent = lazy(() => import("./MarkdownContent").then((module) => ({ default: module.MarkdownContent })));

export function LazyMarkdownContent({ text, streaming = false }: { text: string; streaming?: boolean }) {
  return <Suspense fallback={<p className="message-text">{text}</p>}>
    <MarkdownContent text={text} streaming={streaming} />
  </Suspense>;
}
