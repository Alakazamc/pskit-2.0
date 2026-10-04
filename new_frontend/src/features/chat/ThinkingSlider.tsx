import type { CSSProperties } from "react";
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

export function ThinkingSlider({ levels, effort, onChange }: {
  levels: Effort[];
  effort?: Effort;
  onChange: (effort: Effort | undefined) => void;
}) {
  const { t } = useLanguage();
  const steps = [undefined, ...effortOrder.filter((level) => levels.includes(level))];
  const index = Math.max(0, steps.indexOf(effort));
  const label = (level: Effort | undefined) => t(level ? effortKeys[level] : "composer.effortDefault");
  const position = `${index / (steps.length - 1) * 100}%`;

  return <>
    <div className="composer-thinking-header">
      <div className="composer-model-section-title">{t("composer.thinkingLevel")}</div>
      <strong>{label(steps[index])}</strong>
    </div>
    <div className="composer-effort-control">
      <div className="composer-effort-lever" style={{ "--effort-position": position } as CSSProperties}>
        <div className="composer-effort-face" aria-hidden="true">
          <span className="composer-effort-track"><span /></span>
          <span className="composer-effort-grip" />
        </div>
        <input type="range" min={0} max={steps.length - 1} step={1} value={index}
          aria-label={t("composer.thinkingLevel")} aria-orientation="vertical"
          aria-valuetext={label(steps[index])}
          onChange={(event) => onChange(steps[event.currentTarget.valueAsNumber])} />
      </div>
      <div className="composer-effort-scale" aria-hidden="true">
        {steps.slice().reverse().map((level) => <span key={level ?? "default"} data-active={level === steps[index]}>{label(level)}</span>)}
      </div>
      <div className="composer-effort-guidance">
        <p>{t("composer.thinkingDragHint")}</p>
        <p>{t("composer.thinkingHint")}</p>
      </div>
    </div>
  </>;
}
