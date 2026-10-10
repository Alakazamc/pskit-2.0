import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import type { ResearchApi } from "../../api/types";
import { useLanguage } from "../../i18n/LanguageProvider";
import { adminMeKey, adminSections } from "./adminQueries";

export function AdminEntry({ api, userId }: { api: ResearchApi; userId: string }) {
  const { t } = useLanguage();
  const me = useQuery({ queryKey: adminMeKey(userId), queryFn: api.getAdminMe, enabled: typeof api.getAdminMe === "function", retry: false });
  if (!me.data?.permissions?.length || me.error || !adminSections.some(([, permission]) => me.data.permissions.includes(permission))) return null;
  return <Link className="mono-button" to="/admin">{t("admin.title")}</Link>;
}
