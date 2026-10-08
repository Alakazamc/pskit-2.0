import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, Navigate, NavLink, useParams } from "react-router-dom";
import type { ResearchApi, UserIdentity } from "../../api/types";
import { ApiError } from "../../api/http";
import { LanguageSwitch, useLanguage } from "../../i18n/LanguageProvider";
import { AdminContext, adminMeKey, adminSections } from "./adminQueries";
import { AdminModelsPage } from "./AdminModelsPage";
import { AdminServicesPage } from "./AdminServicesPage";
import { AdminUsersPage } from "./AdminUsersPage";
import { AdminJobsPage } from "./AdminJobsPage";
import { AdminSandboxesPage } from "./AdminSandboxesPage";
import { AdminUsagePage } from "./AdminUsagePage";
import { AdminAuditPage } from "./AdminAuditPage";
import { AdminToolProductsPage } from "./AdminToolProductsPage";
import { AdminSkillsPage } from "./AdminSkillsPage";

export function AdminShell({ api, user, onUnauthorized }: { api: ResearchApi; user: UserIdentity; onUnauthorized: () => void }) {
  const { t } = useLanguage();
  const { section } = useParams();
  const [theme, setTheme] = useState<"light" | "dark">(() => window.localStorage.getItem("pskit-theme") === "light" ? "light" : "dark");
  const principal = useQuery({ queryKey: adminMeKey(user.id), queryFn: api.getAdminMe, retry: false });
  useEffect(() => { document.documentElement.dataset.theme = theme; window.localStorage.setItem("pskit-theme", theme); }, [theme]);
  useEffect(() => {
    if (principal.error instanceof ApiError && principal.error.status === 401) onUnauthorized();
  }, [principal.error, onUnauthorized]);
  if (principal.isPending) return <main className={`mono-app admin-app ${theme}`}><p role="status">{t("admin.loading")}</p></main>;
  if (principal.error || !principal.data?.permissions?.length) return <main className={`mono-app admin-app ${theme}`}><div className="admin-access"><p role="alert">{t(principal.error instanceof ApiError && principal.error.status === 403 ? "admin.forbidden" : "admin.unavailable")}</p><Link to="/">{t("admin.back")}</Link></div></main>;
  const allowed = adminSections.filter(([, permission]) => principal.data.permissions.includes(permission));
  if (!section) return <Navigate to={`/admin/${allowed[0]?.[0] ?? "models"}`} replace />;
  const active = allowed.find(([name]) => name === section);
  return <AdminContext.Provider value={{ api, me: principal.data }}><div className={`mono-app admin-app ${theme}`}>
    <aside className="admin-sidebar"><Link to="/" className="admin-brand">PSKit <span>{t("admin.title")}</span></Link><nav aria-label={t("admin.navigation")}>{allowed.map(([name, , key]) => <NavLink key={name} to={`/admin/${name}`}>{t(key)}</NavLink>)}</nav><Link to="/">{t("admin.back")}</Link></aside>
    <main className="admin-main"><header className="admin-header"><h1>{active ? t(active[2]) : t("admin.title")}</h1><div className="admin-preferences"><LanguageSwitch /><button type="button" aria-label={t(theme === "dark" ? "theme.switchToLight" : "theme.switchToDark")} onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>{t(theme === "dark" ? "mono.light" : "mono.dark")}</button></div></header><div className="admin-content">{!active ? <p role="alert">{t("admin.forbidden")}</p> : section === "models" ? <AdminModelsPage /> : section === "services" ? <AdminServicesPage /> : section === "tool-products" ? <AdminToolProductsPage theme={theme} /> : section === "skills" ? <AdminSkillsPage /> : section === "users" ? <AdminUsersPage /> : section === "jobs" ? <AdminJobsPage /> : section === "sandboxes" ? <AdminSandboxesPage /> : section === "usage" ? <AdminUsagePage /> : <AdminAuditPage />}</div></main>
  </div></AdminContext.Provider>;
}
