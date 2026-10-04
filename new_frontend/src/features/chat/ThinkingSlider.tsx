import { Plane } from "lucide-react";
import { type CSSProperties, type ReactNode, useId } from "react";
import type { MessageRequest } from "../../api/types";
import { useLanguage } from "../../i18n/LanguageProvider";
import type { TranslationKey } from "../../i18n/translations";

type Effort = NonNullable<MessageRequest["reasoning_effort"]>;
const effortOrder: Effort[] = ["off", "minimal", "low", "medium", "high", "xhigh", "max"];
export const effortKeys: Record<Effort, TranslationKey> = {
  off: "composer.effortOff", minimal: "composer.effortMinimal", low: "composer.effortLow",
  medium: "composer.effortMedium", high: "composer.effortHigh",
  xhigh: "composer.effortXhigh", max: "composer.effortMax",
};

export function ThinkingSlider({ levels, effort, onChange, levelControl }: {
  levels: Effort[];
  effort?: Effort;
  onChange: (effort: Effort | undefined) => void;
  levelControl?: ReactNode;
}) {
  const { t } = useLanguage();
  const hintId = useId();
  const steps = [undefined, ...effortOrder.filter((level) => levels.includes(level))];
  const index = Math.max(0, steps.indexOf(effort));
  const label = (level: Effort | undefined) => t(level ? effortKeys[level] : "composer.effortDefault");
  const position = `${index / (steps.length - 1) * 100}%`;

  return <>
    <div className="composer-effort-control">
      <div className="composer-effort-lever" style={{ "--effort-position": position, "--effort-fraction": index / (steps.length - 1) } as CSSProperties}>
        <div className="composer-effort-face" aria-hidden="true">
          <span className="composer-effort-track"><span /></span>
          <span className="composer-effort-grip"><Plane size={26} className="composer-plane-icon" fill="currentColor" strokeWidth={1.3} /></span>
        </div>
        <input type="range" min={0} max={steps.length - 1} step={1} value={index}
          aria-label={t("composer.thinkingLevel")} aria-orientation="vertical"
          aria-valuetext={label(steps[index])}
          aria-describedby={hintId}
          onChange={(event) => onChange(steps[event.currentTarget.valueAsNumber])} />
        <div className="composer-effort-label"><div className="composer-effort-value">{levelControl ?? <span aria-hidden="true">{label(steps[index])}</span>}</div></div>
      </div>
    </div>
    <p id={hintId} className="composer-effort-hint">{t("composer.thinkingDragHint")}</p>
  </>;
}
