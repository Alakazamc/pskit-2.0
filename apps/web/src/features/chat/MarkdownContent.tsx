import { code } from "@streamdown/code";
import { cjk } from "@streamdown/cjk";
import { createMathPlugin } from "@streamdown/math";
import { Streamdown, type Components } from "streamdown";
import { MarkdownTable } from "./MarkdownTable";

const plugins = { code, cjk, math: createMathPlugin({ singleDollarTextMath: true }) };
const controls = { table: false } as const;
const components: Components = {
  strong: ({ children, className }) => <strong className={className}>{children}</strong>,
  table: MarkdownTable,
};

export function MarkdownContent({ text, streaming = false }: { text: string; streaming?: boolean }) {
  // Removed useDeferredValue to reduce streaming latency
  // Streamdown handles its own throttling internally
  return <Streamdown
    className="message-markdown"
    mode={streaming ? "streaming" : "static"}
    isAnimating={streaming}
    plugins={plugins}
    controls={controls}
    tableMaxHeight={0}
    components={components}
    skipHtml
  >{text}</Streamdown>;
}
