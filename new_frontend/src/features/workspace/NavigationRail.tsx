import { Atom, BookOpenText, FolderKanban, LayoutGrid, Settings2 } from "lucide-react";
import { NavLink } from "react-router-dom";
import { useLanguage } from "../../i18n/LanguageProvider";

export function NavigationRail() {
  const { t } = useLanguage();
  return <nav className="navigation-rail" aria-label={t("nav.main")}>
    <div className="rail-logo"><Atom size={23} /></div>
    <div className="rail-group">
      <NavLink to="/g" title={t("nav.projects")} className={({ isActive }) => `rail-link ${isActive ? "active" : ""}`}><LayoutGrid size={20} /><span>{t("nav.projects")}</span></NavLink>
      <NavLink to="/skills" title={t("nav.skills")} className={({ isActive }) => `rail-link ${isActive ? "active" : ""}`}><BookOpenText size={20} /><span>{t("nav.skills")}</span></NavLink>
      <NavLink to="/resources" title={t("nav.resources")} className={({ isActive }) => `rail-link ${isActive ? "active" : ""}`}><FolderKanban size={20} /><span>{t("nav.resources")}</span></NavLink>
    </div>
    <div className="rail-spacer" />
    <NavLink to="/settings" title={t("nav.settings")} className={({ isActive }) => `rail-link ${isActive ? "active" : ""}`}><Settings2 size={20} /><span>{t("nav.settings")}</span></NavLink>
  </nav>;
}
