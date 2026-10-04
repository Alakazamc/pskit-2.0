import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import type { ResearchApi } from "../../api/types";
import { useLanguage } from "../../i18n/LanguageProvider";

export function UsageActivity({ api, userId }: { api: ResearchApi; userId: string }) {
  const { language, t } = useLanguage();
  const activity = useQuery({ queryKey: ["usage-activity", userId], queryFn: api.getUsageActivity });
  const [metric, setMetric] = useState<"tokens" | "gpu">("tokens");
  const [selectedDate, setSelectedDate] = useState<string>();
  const scroll = useRef<HTMLDivElement>(null);
  const cells = useRef<(HTMLButtonElement | null)[]>([]);
  const days = activity.data?.days;
  useEffect(() => { if (scroll.current) scroll.current.scrollLeft = scroll.current.scrollWidth; }, [days]);
  const locale = language === "zh" ? "zh-CN" : "en-US";
  const value = (day: NonNullable<typeof days>[number]) => metric === "tokens" ? day.tokens : day.gpu_ms / 60000;
  const number = (count: number) => new Intl.NumberFormat(locale, { maximumFractionDigits: 2 }).format(count);
  const unit = metric === "tokens" ? "Token" : t("activity.gpuMinutes");
  const selectedIndex = days?.findIndex((day) => day.date === selectedDate) ?? -1;
  const focusedIndex = selectedIndex >= 0 ? selectedIndex : (days?.length ?? 1) - 1;
  const selected = days?.[focusedIndex];
  const max = Math.max(0, ...(days ?? []).map(value));
  const total = (days ?? []).reduce((sum, day) => sum + value(day), 0);
  const firstDay = days?.[0] ? new Date(`${days[0].date}T00:00:00Z`) : null;
  const padding = firstDay ? (firstDay.getUTCDay() + 6) % 7 : 0;
  const columns = Math.ceil(((days?.length ?? 0) + padding) / 7);
  const months = new Map<number, string>();
  let previousMonth = "";
  days?.forEach((day, index) => {
    const month = day.date.slice(0, 7);
    if (month !== previousMonth) {
      const column = Math.floor((index + padding) / 7);
      months.set(column, new Intl.DateTimeFormat(locale, { month: "short", timeZone: "UTC" }).format(new Date(`${day.date}T00:00:00Z`)));
      previousMonth = month;
    }
  });
  return <div className="usage-activity" aria-busy={activity.isPending}>
    <div className="usage-activity-heading"><h3>{t(metric === "tokens" ? "activity.tokens" : "activity.gpu")}</h3><div className="usage-metric-tabs" role="group" aria-label={t("activity.metric")}>
      <button type="button" aria-label={t("activity.tokens")} aria-pressed={metric === "tokens"} onClick={() => setMetric("tokens")}>Token</button>
      <button type="button" aria-label={t("activity.gpu")} aria-pressed={metric === "gpu"} onClick={() => setMetric("gpu")}>GPU</button>
    </div></div>
    {activity.isPending && <p role="status">{t("usage.loading")}</p>}
    {activity.isError && <p role="alert">{t("activity.failed")}</p>}
    {activity.data && <>
      <div className="usage-activity-summary"><strong>{number(total)} <span>{unit}</span></strong><span>{t("activity.year")}</span></div>
      <div className="usage-activity-scroll" ref={scroll}>
        <div className="usage-activity-calendar" style={{ "--activity-columns": columns } as React.CSSProperties}>
          <div className="usage-activity-grid" role="group" aria-label={t("activity.calendar")}>
            {Array.from({ length: padding }, (_, index) => <span className="usage-activity-blank" key={`pad-${index}`} />)}
            {days?.map((day, index) => {
              const amount = value(day);
              const label = `${day.date}${language === "zh" ? "：" : ": "}${number(amount)} ${unit}`;
              const level = amount > 0 && max > 0 ? Math.max(1, Math.ceil(amount / max * 4)) : 0;
              return <button type="button" key={day.date} ref={(element) => { cells.current[index] = element; }}
                className="usage-activity-cell" data-date={day.date} data-level={level} aria-label={label} title={label}
                tabIndex={index === focusedIndex ? 0 : -1} onFocus={() => setSelectedDate(day.date)}
                onMouseEnter={() => setSelectedDate(day.date)} onClick={() => setSelectedDate(day.date)}
                onKeyDown={(event) => {
                  const delta: Record<string, number> = { ArrowUp: -1, ArrowDown: 1, ArrowLeft: -7, ArrowRight: 7 };
                  let next: number;
                  if (event.key === "Home") next = 0;
                  else if (event.key === "End") next = (days?.length ?? 1) - 1;
                  else if (event.key in delta) next = index + delta[event.key];
                  else return;
                  event.preventDefault();
                  next = Math.max(0, Math.min((days?.length ?? 1) - 1, next));
                  cells.current[next]?.focus();
                }} />;
            })}
          </div>
          <div className="usage-activity-months" aria-hidden="true">{[...months].map(([column, label]) => <span style={{ gridColumn: column + 1 }} key={column}>{label}</span>)}</div>
        </div>
      </div>
      <div className="usage-activity-footer"><output data-testid="usage-activity-details" className="usage-activity-details">{selected && `${selected.date} · ${number(value(selected))} ${unit}`}</output><div className="usage-activity-legend" aria-label={t("activity.scale")}><span>{t("activity.less")}</span>{[0, 1, 2, 3, 4].map((level) => <i key={level} data-level={level} aria-hidden="true" />)}<span>{t("activity.more")}</span></div></div>
      {total === 0 && <p className="usage-activity-empty">{t("activity.empty")}</p>}
    </>}
  </div>;
}
