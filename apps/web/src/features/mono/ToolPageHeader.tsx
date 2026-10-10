import { ArrowLeft, PenLine } from "lucide-react";
import { createContext, useContext, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useLanguage } from "../../i18n/LanguageProvider";

const HeaderTarget = createContext<HTMLElement | null | undefined>(undefined);

export function ToolPageHeaderProvider({ target, children }: { target: HTMLElement | null; children: ReactNode }) {
  return <HeaderTarget.Provider value={target}>{children}</HeaderTarget.Provider>;
}

export function ToolPageHeader({ title, description, loading = false, onBack, onEdit }: {
  title: string; description?: string; loading?: boolean; onBack: () => void; onEdit?: () => void;
}) {
  const { t } = useLanguage();
  const target = useContext(HeaderTarget);
  const heading = <div className="mono-tool-page-heading">
    <button type="button" className="mono-tool-back" aria-label={t("tools.back")} title={t("tools.back")} onClick={onBack}><ArrowLeft size={19} aria-hidden="true" /></button>
    <div className="mono-tool-page-title">{loading
      ? <div className="mono-tool-title-placeholder" aria-hidden="true"><span /><span /></div>
      : <><h1>{title}</h1>{description && <p>{description}</p>}</>}</div>
    {onEdit && <button type="button" className="mono-tool-edit" aria-label={t("catalog.edit")} title={t("catalog.edit")} onClick={onEdit}><PenLine size={17} aria-hidden="true" /></button>}
  </div>;
  // Standalone tool pages retain their heading; the workspace owns its placement.
  if (target === undefined) return <header className="mono-tool-standalone-header">{heading}</header>;
  return target ? createPortal(heading, target) : null;
}
