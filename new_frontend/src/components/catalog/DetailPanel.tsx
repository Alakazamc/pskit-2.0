import * as Dialog from "@radix-ui/react-dialog";
import { PenLine, X } from "lucide-react";
import { useRef, type ReactNode } from "react";
import { useWorkspacePortalContainer } from "../../hooks/useWorkspacePortalContainer";
import { useLanguage } from "../../i18n/LanguageProvider";

export function DetailPanel({ open, onOpenChange, title, description, onEdit, children, className = "" }: {
  open: boolean; onOpenChange: (open: boolean) => void; title: string;
  description?: string; onEdit?: () => void; children: ReactNode;
  className?: string;
}) {
  const { t } = useLanguage();
  const portalContainer = useWorkspacePortalContainer();
  const opener = useRef<HTMLElement | null>(null);
  return <Dialog.Root open={open} onOpenChange={onOpenChange}><Dialog.Portal container={portalContainer}>
    <Dialog.Overlay className="mono-dialog-overlay" />
    <Dialog.Content className={`catalog-detail-panel ${className}`.trim()} onOpenAutoFocus={() => {
      opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    }} onCloseAutoFocus={(event) => { event.preventDefault(); opener.current?.focus(); }}>
      <div className="catalog-detail-heading"><Dialog.Title>{title}</Dialog.Title><div>
        {onEdit && <button type="button" aria-label={t("catalog.edit")} title={t("catalog.edit")} onClick={onEdit}><PenLine size={17} /></button>}
        <Dialog.Close aria-label={t("catalog.close")}><X size={18} /></Dialog.Close>
      </div></div>
      <Dialog.Description className={description ? "catalog-detail-description" : "sr-only"}>{description || title}</Dialog.Description>
      <div className="catalog-detail-body">{children}</div>
    </Dialog.Content>
  </Dialog.Portal></Dialog.Root>;
}
