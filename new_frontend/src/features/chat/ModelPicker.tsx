import * as Popover from "@radix-ui/react-popover";
import { Check, ChevronDown, Search } from "lucide-react";
import { useState } from "react";
import type { MessageRequest, ModelOption } from "../../api/types";
import { useWorkspacePortalContainer } from "../../hooks/useWorkspacePortalContainer";
import { useLanguage } from "../../i18n/LanguageProvider";

type Effort = NonNullable<MessageRequest["reasoning_effort"]>;
const effortKeys: Record<Effort, "composer.effortOff" | "composer.effortMinimal" | "composer.effortLow" | "composer.effortMedium" | "composer.effortHigh" | "composer.effortXhigh" | "composer.effortMax"> = {
  off: "composer.effortOff", minimal: "composer.effortMinimal", low: "composer.effortLow",
  medium: "composer.effortMedium", high: "composer.effortHigh",
  xhigh: "composer.effortXhigh", max: "composer.effortMax",
};

function modelLabel(id: string): string {
  return id.split("/").at(-1) ?? id;
}

function providerLabel(id: string): string {
  return id.includes("/") ? id.split("/")[0] : "";
}

export function ModelPicker({ models, selected, effort, hasImages, selectionUnavailable = false, onModelChange, onEffortChange }: {
  models: ModelOption[];
  selected?: ModelOption;
  effort?: Effort | null;
  hasImages: boolean;
  selectionUnavailable?: boolean;
  onModelChange: (id: string) => void;
  onEffortChange: (effort: Effort | undefined) => void;
}) {
  const { t } = useLanguage();
  const portalContainer = useWorkspacePortalContainer();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const levels = selected?.reasoning_levels ?? [];
  const activeEffort = effort && levels.includes(effort) ? effort : undefined;
  const matches = models.filter((option) => option.id.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const providers = [...new Set(matches.map((option) => providerLabel(option.id)))].sort((a, b) =>
    Number(b === providerLabel(selected?.id ?? "")) - Number(a === providerLabel(selected?.id ?? "")) || a.localeCompare(b));
  const label = selected ? modelLabel(selected.id) : t("composer.selectModel");
  const effortLabel = t(activeEffort ? effortKeys[activeEffort] : "composer.effortDefault");
  return <Popover.Root open={open} onOpenChange={(next) => { setOpen(next); if (!next) setQuery(""); }}>
    <Popover.Trigger asChild><button type="button" className="composer-model-trigger" aria-label={`${t("composer.chooseModel")}: ${label} · ${effortLabel}`} title={selected?.id}>
      <span>{label}</span><span className="composer-model-effort">{effortLabel}</span><ChevronDown size={14} aria-hidden="true" />
    </button></Popover.Trigger>
    <Popover.Portal container={portalContainer}><Popover.Content className="composer-model-popover" side="top" sideOffset={9} align="start" collisionPadding={12}>
      <div className="composer-model-search"><Search size={15} aria-hidden="true" /><input type="search" aria-label={t("composer.searchModels")} placeholder={t("composer.searchModels")} value={query} onChange={(event) => setQuery(event.target.value)} /></div>
      <div className="composer-model-list">
        {selectionUnavailable && <p className="composer-model-empty" role="status">{t("composer.modelUnavailable")}</p>}
        {matches.length === 0 && <p className="composer-model-empty">{t(models.length ? "composer.noMatchingModels" : "composer.noModels")}</p>}
        {providers.map((provider) => <div className="composer-model-group" key={provider}>
          <div className="composer-model-group-label">{provider || t("composer.otherModels")}</div>
          {matches.filter((option) => providerLabel(option.id) === provider).sort((a, b) => Number(b.id === selected?.id) - Number(a.id === selected?.id)).map((option) => {
            const blocked = hasImages && !option.supports_images;
            return <button key={option.id} type="button" className="composer-model-option" aria-label={option.id} aria-pressed={option.id === selected?.id} disabled={blocked} title={blocked ? t("composer.modelNeedsImages") : option.id} onClick={() => { if (option.id !== selected?.id) { onModelChange(option.id); onEffortChange(undefined); } }}>
              <span className="composer-model-option-text"><strong>{modelLabel(option.id)}</strong><small>{option.id}{option.supports_images ? ` · ${t("composer.supportsImages")}` : ""}</small></span>
              {option.id === selected?.id && <Check size={16} aria-hidden="true" />}
            </button>;
          })}
        </div>)}
      </div>
      <div className="composer-model-thinking">
        <div className="composer-model-section-title">{t("composer.thinkingLevel")}</div>
        {levels.length === 0 ? <p>{t("composer.noThinkingLevels")}</p> : <>
          <div className="composer-effort-options" role="radiogroup" aria-label={t("composer.thinkingLevel")}>
            <label><input type="radio" name="composer-thinking-level" checked={!activeEffort} onChange={() => onEffortChange(undefined)} />{t("composer.effortDefault")}</label>
            {levels.map((level) => <label key={level}><input type="radio" name="composer-thinking-level" checked={activeEffort === level} onChange={() => onEffortChange(level)} />{t(effortKeys[level])}</label>)}
          </div>
          <p>{t("composer.thinkingHint")}</p>
        </>}
      </div>
    </Popover.Content></Popover.Portal>
  </Popover.Root>;
}
