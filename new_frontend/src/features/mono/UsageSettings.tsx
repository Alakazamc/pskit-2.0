import { useQuery } from "@tanstack/react-query";
import type { UsageEntry, ResearchApi } from "../../api/types";
import { useLanguage } from "../../i18n/LanguageProvider";
import type { TranslationKey } from "../../i18n/translations";
import { UsageActivity } from "./UsageActivity";

const statusKeys: Record<string, TranslationKey> = {
  pending_reconciliation: "usage.statusPending",
  reconciled: "usage.statusReconciled",
  queued: "usage.statusQueued",
  running: "usage.statusRunning",
  completed: "usage.statusCompleted",
  failed: "usage.statusFailed",
  cancelled: "usage.statusCancelled",
  posted: "usage.statusPosted",
  reserved: "usage.statusReserved",
  released: "usage.statusReleased",
};

export function UsageSettings({ api, userId, isGuest = false }: { api: ResearchApi; userId: string; isGuest?: boolean }) {
  const { language, t } = useLanguage();
  const usage = useQuery({ queryKey: ["usage", userId], queryFn: api.getUsage });
  const entries = useQuery({ queryKey: ["usage-entries", userId], queryFn: api.getUsageEntries });
  const locale = language === "zh" ? "zh-CN" : "en-US";
  const number = (value: number) => new Intl.NumberFormat(locale).format(value);
  const date = (value: string) => new Intl.DateTimeFormat(locale, {
    year: "numeric", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
  }).format(new Date(value));
  const recent = [...(entries.data ?? [])]
    .sort((a, b) => b.created_at.localeCompare(a.created_at))
    .slice(0, 8);
  const entryLabel = (entry: UsageEntry) => entry.resource === "tokens"
    ? t("usage.tokens") : t("usage.gpu");

  return <section className="mono-panel mono-settings-panel mono-usage-panel" aria-labelledby="usage-settings-title">
    <h2 id="usage-settings-title">{t("usage.title")}</h2>
    <p>{t("usage.settingsDescription")}</p>
    {isGuest && <p className="guest-usage-note">{t("guest.usageNote")}</p>}
    {usage.isPending && <p role="status">{t("usage.loading")}</p>}
    {usage.isError && <p role="alert">{t("usage.loadFailed")}</p>}
    {usage.data && <div className="mono-quota-grid">
      <div className="mono-quota-item">
        <span>{t("usage.tokens")}</span>
        <strong>{number(usage.data.tokens.remaining)}</strong>
        <small>{t("usage.remainingOf", { limit: number(usage.data.tokens.limit) })}</small>
        <p>{t("usage.usedReserved", { used: number(usage.data.tokens.used), reserved: number(usage.data.tokens.reserved) })}</p>
        <small>{t("usage.resetsAt", { date: date(usage.data.tokens.resets_at) })}</small>
      </div>
      <div className="mono-quota-item">
        <span>{t("usage.gpu")}</span>
        <strong>{number(usage.data.gpu.remaining)} <small>{t("usage.minutes")}</small></strong>
        <small>{t("usage.remainingOf", { limit: number(usage.data.gpu.limit) })}</small>
        <p>{t("usage.usedReserved", { used: number(usage.data.gpu.used), reserved: number(usage.data.gpu.reserved) })}</p>
        <small>{t("usage.resetsAt", { date: date(usage.data.gpu.resets_at) })}</small>
      </div>
    </div>}
    <UsageActivity key={userId} api={api} userId={userId} />
    <div className="mono-usage-history">
      <h3>{t("usage.recent")}</h3>
      {entries.isPending && <p role="status">{t("usage.loading")}</p>}
      {entries.isError && <p role="alert">{t("usage.loadFailed")}</p>}
      {entries.isSuccess && recent.length === 0 && <p>{t("usage.empty")}</p>}
      {recent.length > 0 && <ul>{recent.map((entry) => <li key={entry.id}>
        <div><b>{entryLabel(entry)}</b><time dateTime={entry.created_at}>{date(entry.created_at)}</time></div>
        <div><strong>{number(entry.amount)} {entry.resource === "tokens" ? "Token" : t("usage.minutes")}</strong><span className={entry.status === "pending_reconciliation" ? "mono-usage-pending" : undefined}>{t(statusKeys[entry.status] ?? "usage.statusOther")}</span></div>
      </li>)}</ul>}
    </div>
  </section>;
}
