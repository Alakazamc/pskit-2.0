import { LazyMarkdownContent } from "./LazyMarkdownContent";

function isMarkdown(name: string): boolean {
  return /\.md$/i.test(name);
}

/** Render Markdown artifacts with the same safe rich renderer used in chat. */
export function ArtifactContentPreview({ name, text }: { name: string; text: string }) {
  if (isMarkdown(name)) {
    return <div className="artifact-rich-preview"><LazyMarkdownContent text={text} /></div>;
  }
  return <pre className="artifact-preview-text">{text}</pre>;
}
