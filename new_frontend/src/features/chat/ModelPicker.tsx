import * as Popover from "@radix-ui/react-popover";
import { Check, ChevronDown, Plane, Search } from "lucide-react";
import { useState } from "react";
import type { MessageRequest, ModelOption } from "../../api/types";
import { useWorkspacePortalContainer } from "../../hooks/useWorkspacePortalContainer";
import { useLanguage } from "../../i18n/LanguageProvider";
import { effortKeys, ThinkingSlider } from "./ThinkingSlider";

type Effort = NonNullable<MessageRequest["reasoning_effort"]>;

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
  const [modelsOpen, setModelsOpen] = useState(false);
  const [query, setQuery] = useState("");
  const levels = selected?.reasoning_levels ?? [];
  const activeEffort = effort && levels.includes(effort) ? effort : undefined;
  const matches = models.filter((option) => option.id.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const providers = [...new Set(matches.map((option) => providerLabel(option.id)))].sort((a, b) =>
    Number(b === providerLabel(selected?.id ?? "")) - Number(a === providerLabel(selected?.id ?? "")) || a.localeCompare(b));
  const label = selected ? modelLabel(selected.id) : t("composer.selectModel");
  const effortLabel = t(activeEffort ? effortKeys[activeEffort] : "composer.effortDefault");
  return <Popover.Root open={open} onOpenChange={(next) => { setOpen(next); if (!next) { setModelsOpen(false); setQuery(""); } }}>
    <Popover.Trigger asChild><button type="button" className="composer-model-trigger" aria-label={`${t("composer.chooseModel")}: ${label} · ${effortLabel}`} title={selected?.id}>
      <span>{label}</span><span className="composer-model-effort">{effortLabel}</span><ChevronDown size={14} aria-hidden="true" />
    </button></Popover.Trigger>
    <Popover.Portal container={portalContainer}><Popover.Content className="composer-settings-popover" aria-label={t("composer.modelSettings")} side="top" sideOffset={9} align="end" collisionPadding={12}>
      <Popover.Root open={modelsOpen} onOpenChange={(next) => { setModelsOpen(next); if (!next) setQuery(""); }}>
        <Popover.Trigger asChild><button type="button" className="composer-settings-model" aria-label={`${t("composer.switchModel")}: ${label}`} title={selected?.id}>
          <Plane size={19} className="composer-plane-icon" fill="currentColor" strokeWidth={1.4} aria-hidden="true" /><span>{t("composer.model")}</span><ChevronDown size={16} aria-hidden="true" />
        </button></Popover.Trigger>
        <Popover.Portal container={portalContainer}><Popover.Content className="composer-model-popover" aria-label={t("composer.searchModels")} side="bottom" sideOffset={8} align="end" collisionPadding={12}>
          <div className="composer-model-search"><Search size={15} aria-hidden="true" /><input type="search" aria-label={t("composer.searchModels")} placeholder={t("composer.searchModels")} value={query} onChange={(event) => setQuery(event.target.value)} /></div>
          <div className="composer-model-list">
            {matches.length === 0 && <p className="composer-model-empty">{t(models.length ? "composer.noMatchingModels" : "composer.noModels")}</p>}
            {providers.map((provider) => <div className="composer-model-group" key={provider}>
              <div className="composer-model-group-label">{provider || t("composer.otherModels")}</div>
              {matches.filter((option) => providerLabel(option.id) === provider).sort((a, b) => Number(b.id === selected?.id) - Number(a.id === selected?.id)).map((option) => {
                const blocked = hasImages && !option.supports_images;
                return <button key={option.id} type="button" className="composer-model-option" aria-label={option.id} aria-pressed={option.id === selected?.id} disabled={blocked} title={blocked ? t("composer.modelNeedsImages") : option.id} onClick={() => { if (option.id !== selected?.id) { onModelChange(option.id); onEffortChange(undefined); } setModelsOpen(false); setQuery(""); }}>
                  <span className="composer-model-option-text"><strong>{modelLabel(option.id)}</strong><small>{option.id}{option.supports_images ? ` · ${t("composer.supportsImages")}` : ""}</small></span>
                  {option.id === selected?.id && <Check size={16} aria-hidden="true" />}
                </button>;
              })}
            </div>)}
          </div>
        </Popover.Content></Popover.Portal>
      </Popover.Root>
      {selectionUnavailable && <p className="composer-model-empty" role="status">{t("composer.modelUnavailable")}</p>}
      <div className="composer-model-thinking">
        {levels.length === 0 ? <>
          <div className="composer-model-section-title">{t("composer.thinkingLevel")}</div>
          <p>{t(models.length ? "composer.noThinkingLevels" : "composer.noModels")}</p>
        </> : <ThinkingSlider levels={levels} effort={activeEffort} onChange={onEffortChange} />}
      </div>
    </Popover.Content></Popover.Portal>
  </Popover.Root>;
}
