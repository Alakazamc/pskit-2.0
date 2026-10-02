import { Cpu, Gauge, Info } from "lucide-react";
import type { UsageSnapshot } from "../../api/types";
import { useLanguage } from "../../i18n/LanguageProvider";

export function UsageCard({ usage }: { usage: UsageSnapshot | undefined }) {
  const { language, t } = useLanguage();
  const locale = language === "zh" ? "zh-CN" : "en-US";
  const number = (value: number) => new Intl.NumberFormat(locale).format(value);
  const reset = (value: string) => new Intl.DateTimeFormat(locale, { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
  if (!usage) return <div className="usage-card skeleton">{t("usage.loading")}</div>;
  return <div className="usage-card">
    <div className="usage-heading"><span>{t("usage.title")}</span><Info size={14} /></div>
    <div className="usage-row"><Gauge size={16} /><div><span>{t("usage.tokens")}</span><b>{number(usage.tokens.remaining)} <small>/ {number(usage.tokens.limit)}</small></b></div></div>
    <div className="usage-track"><span style={{ width: `${usage.tokens.limit ? Math.min(100, usage.tokens.used / usage.tokens.limit * 100) : 0}%` }} /></div>
    <div className="usage-subline">{t("usage.tokenNote")}</div>
    <div className="usage-row gpu"><Cpu size={16} /><div><span>{t("usage.gpu")}</span><b>{number(usage.gpu.remaining)} <small>/ {number(usage.gpu.limit)} {t("usage.minutes")}</small></b></div></div>
    <div className="usage-subline">{t("usage.summary", { used: number(usage.gpu.used), reserved: number(usage.gpu.reserved), date: reset(usage.gpu.resets_at) })}</div>
  </div>;
}
