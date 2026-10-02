import * as Dialog from "@radix-ui/react-dialog";
import { Check, Copy, Maximize2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { extractTableDataFromElement, tableDataToMarkdown, type Components } from "streamdown";
import { useLanguage } from "../../i18n/LanguageProvider";

export const MarkdownTable: Components["table"] = ({ children, className, node: _node, ...tableProps }) => {
  void _node;
  const { t } = useLanguage();
  const tableRef = useRef<HTMLTableElement>(null);
  const resetTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [portalContainer, setPortalContainer] = useState<HTMLDivElement | null>(null);
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");

  useEffect(() => () => { if (resetTimer.current) clearTimeout(resetTimer.current); }, []);

  const copyTable = async () => {
    if (!tableRef.current) return;
    try {
      const markdown = tableDataToMarkdown(extractTableDataFromElement(tableRef.current));
      await navigator.clipboard.writeText(markdown);
      setCopyState("copied");
    } catch {
      setCopyState("failed");
    }
    if (resetTimer.current) clearTimeout(resetTimer.current);
    resetTimer.current = setTimeout(() => setCopyState("idle"), 2000);
  };

  const copyLabel = t(copyState === "copied" ? "conversation.copied" : copyState === "failed" ? "conversation.copyFailed" : "conversation.copyTable");
  const copyIcon = copyState === "copied" ? <Check size={17} aria-hidden="true" /> : <Copy size={17} aria-hidden="true" />;

  return <Dialog.Root>
    <div className="markdown-table-wrap" ref={setPortalContainer}>
      <div className="markdown-table-scroll">
        <table {...tableProps} ref={tableRef} className={className} data-streamdown="table">{children}</table>
      </div>
      <div className="markdown-table-actions">
        <button type="button" aria-label={copyLabel} title={copyLabel} onClick={() => void copyTable()}>{copyIcon}</button>
        <Dialog.Trigger asChild><button type="button" aria-label={t("conversation.expandTable")} title={t("conversation.expandTable")}><Maximize2 size={17} aria-hidden="true" /></button></Dialog.Trigger>
      </div>
      <Dialog.Portal container={portalContainer ?? undefined}>
        <Dialog.Overlay className="markdown-table-overlay" />
        <Dialog.Content className="markdown-table-dialog" aria-describedby={undefined}>
          <Dialog.Title className="markdown-table-dialog-title">{t("conversation.tablePreview")}</Dialog.Title>
          <div className="markdown-table-dialog-actions">
            <button type="button" aria-label={copyLabel} title={copyLabel} onClick={() => void copyTable()}>{copyIcon}</button>
            <Dialog.Close asChild><button type="button" aria-label={t("conversation.closeTable")} title={t("conversation.closeTable")}><X size={18} aria-hidden="true" /></button></Dialog.Close>
          </div>
          <div className="markdown-table-dialog-scroll">
            <table className={className} data-streamdown="table">{children}</table>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </div>
  </Dialog.Root>;
};
