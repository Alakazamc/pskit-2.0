import type { CSSProperties, FormEvent, ReactNode } from "react";
import type { ArtifactRef } from "../../api/types";
import { useLanguage } from "../../i18n/LanguageProvider";
import { ToolInputRenderer } from "./ToolInputRenderer";
import { ToolResultRenderer } from "./ToolResultRenderer";
import { ToolRunOverview } from "./ToolRunOverview";
import {
  conditionMatches,
  formWithDefaults,
  localized,
  resolveActionTarget,
  type ToolEvent,
  type ToolForm,
  type ToolRun,
  type ToolUiSchema,
} from "./toolUiSchema";

export type ToolUiRendererProps = {
  schema: ToolUiSchema;
  form: ToolForm;
  run: ToolRun | null;
  events: ToolEvent[];
  onChange: (form: ToolForm) => void;
  onAction: (actionId: string, form: ToolForm) => void;
  onArtifactDownload?: (artifact: ArtifactRef) => void;
  theme?: "dark" | "light";
  showHeader?: boolean;
  resultActions?: ReactNode;
};

export function ToolUiRenderer({ schema, form, run, events, onChange, onAction, onArtifactDownload, theme = "light", showHeader = true, resultActions }: ToolUiRendererProps) {
  const { language, t } = useLanguage();
  const effectiveForm = formWithDefaults(schema, form);
  const document = { form: effectiveForm, run };
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const submitter = (event.nativeEvent as SubmitEvent).submitter as HTMLButtonElement | null;
    const action = schema.actions.find((candidate) => candidate.id === submitter?.value);
    if (!action) return;
    const target = resolveActionTarget(action, document);
    if (target) onAction(target, effectiveForm);
  };
  const status = run?.status;
  const title = localized(schema.product.title, language);
  return <section className="tool-ui-workspace" aria-label={showHeader ? undefined : title} aria-labelledby={showHeader ? "tool-product-title" : undefined} data-layout={schema.page.layout}>
    {showHeader && <header className="tool-ui-header"><h1 id="tool-product-title">{title}</h1>
      <p>{localized(schema.product.description, language)}</p></header>}
    <form className="tool-ui-layout" onSubmit={submit} style={{
      "--tool-ui-input": `${schema.page.input_width ?? 5}fr`,
      "--tool-ui-result": `${schema.page.result_width ?? 7}fr`,
    } as CSSProperties}>
      <div className="tool-ui-input">
        <div className="tool-ui-pane-heading"><h2>{t("toolProduct.newRun")}</h2><p>{t("toolProduct.newRunHelp")}</p></div>
        <ToolInputRenderer schema={schema} form={effectiveForm} onChange={onChange} />
        <div className="tool-ui-actions">{schema.actions.map((action) => conditionMatches(action.visible_when, document) &&
          <button className={`mono-button ${run?.status === "completed" ? "secondary" : "primary"}`} type="submit" name="tool-action" value={action.id} key={action.id}>{localized(action.label, language)}</button>)}</div>
      </div>
      <div className="tool-ui-output">
        {run && <ToolRunOverview schema={schema} run={run} actions={resultActions} />}
        {status && ["failed", "cancelled"].includes(status) && <div className="tool-ui-error" role="alert">{language === "en" ? `Run ${status}` : status === "failed" ? "运行失败" : "运行已取消"}</div>}
        {run ? <ToolResultRenderer schema={schema} form={run.arguments ?? {}} run={run} events={events} onArtifactDownload={onArtifactDownload} theme={theme} />
          : <div className="tool-ui-placeholder">{language === "en" ? "Run the tool to see results" : "运行工具后在这里查看结果"}</div>}
      </div>
    </form>
  </section>;
}
