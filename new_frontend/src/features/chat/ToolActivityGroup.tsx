import { useMemo, useState, type MouseEvent } from "react";
import { ChevronDown, List } from "lucide-react";
import { useLanguage } from "../../i18n/LanguageProvider";
import { activityStatus, activityTool, collectToolActivities, groupActivityStatus, toolActivityKind, type ToolActivityKind, type ToolActivityPart, type ToolActivityStatus } from "./toolActivity";
import { ActivityStatusIcon, activityLabel, activityStatusLabel, activitySummary, ToolActivityRow } from "./toolRegistry";

export type { ToolActivityPart } from "./toolActivity";

type KindCount = { kind: ToolActivityKind | "result"; tool: string; count: number };

export function ToolActivityGroup({ parts }: { parts: ToolActivityPart[] }) {
  const { t } = useLanguage();
  const activities = useMemo(() => collectToolActivities(parts), [parts]);
  const [userOpen, setUserOpen] = useState<boolean | null>(null);
  const single = activities.length === 1;
  const status = groupActivityStatus(activities);
  const hasRunning = activities.some(activity => activityStatus(activity) === "running");
  const open = single || (userOpen ?? hasRunning);

  const { kinds, statuses } = useMemo(() => {
    const kinds = new Map<string, KindCount>();
    const statuses = new Map<ToolActivityStatus, number>();
    for (const activity of activities) {
      const tool = activityTool(activity);
      const kind = activity.call ? toolActivityKind(tool) : "result";
      const key = kind === "unknown" ? `unknown:${tool}` : kind;
      const previous = kinds.get(key);
      const count = activity.call ? 1 : activity.results.length;
      if (previous) previous.count += count;
      else kinds.set(key, { kind, tool, count });
      const state = activityStatus(activity);
      statuses.set(state, (statuses.get(state) ?? 0) + 1);
    }
    return { kinds: [...kinds.values()], statuses: [...statuses.entries()] };
  }, [activities]);

  if (activities.length === 0) return null;

  const counts = kinds.map(({ kind, tool, count }) => t("toolActivity.count", {
    label: kind === "unknown" ? tool || t("toolActivity.unknownTool") : kind === "result" ? t("toolActivity.result") : t(`toolActivity.action.${kind}`),
    count,
  })).join(" · ");
  const statusSummary = statuses.map(([state, count]) => t("toolActivity.count", { label: t(`toolActivity.status.${state}`), count })).join(" · ");
  const attention = activities.filter(activity => {
    const state = activityStatus(activity);
    return state === "failed" || state === "approval_required";
  });
  const toggle = (event: MouseEvent<HTMLElement>) => {
    event.preventDefault();
    setUserOpen(!open);
  };

  return <div className="tool-activity-group" data-single={single} data-status={status}>
    <details className="tool-activity-group__disclosure" open={open}>
      <summary className="tool-activity-group__summary" hidden={single} onClick={toggle}>
        {hasRunning ? <ActivityStatusIcon status="running" /> : <List className="tool-activity-group__icon" size={15} aria-hidden="true" />}
        <span className="tool-activity-group__label">{t("toolActivity.group")}</span>
        <span className="tool-activity-group__counts" title={counts}>{counts}</span>
        <span className="tool-activity-group__status" title={statusSummary}>{statusSummary}</span>
        <ChevronDown className="tool-activity-group__chevron" size={13} aria-hidden="true" />
      </summary>
      <div className="tool-activity-group__list" role={single ? undefined : "region"} tabIndex={single ? undefined : 0} aria-label={single ? undefined : t("toolActivity.list")}>
        {activities.map(activity => <ToolActivityRow key={activity.key} activity={activity} visible={open} onUserToggle={single ? setUserOpen : undefined} />)}
      </div>
    </details>
    {!single && !open && attention.length > 0 ? <div className="tool-activity-group__attention">
      {attention.map(activity => <div className="tool-activity-group__attention-item" key={activity.key} data-status={activityStatus(activity)}>
        <ActivityStatusIcon status={activityStatus(activity)} />
        <span className="tool-activity-group__attention-label">{activityLabel(activity, t)} · {activityStatusLabel(activity, t)}</span>
        <span className="tool-activity-group__attention-summary" title={activitySummary(activity, t)}>{activitySummary(activity, t)}</span>
      </div>)}
    </div> : null}
  </div>;
}
