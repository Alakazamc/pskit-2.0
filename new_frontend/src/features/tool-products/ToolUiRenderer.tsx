import type { CSSProperties, FormEvent } from "react";
import { useLanguage } from "../../i18n/LanguageProvider";
import { ToolInputRenderer } from "./ToolInputRenderer";
import { ToolResultRenderer } from "./ToolResultRenderer";
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
  theme?: "dark" | "light";
};

export function ToolUiRenderer({ schema, form, run, events, onChange, onAction, theme = "light" }: ToolUiRendererProps) {
  const { language } = useLanguage();
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
  return <article className="tool-ui-workspace" aria-labelledby="tool-product-title" data-layout={schema.page.layout}>
    <header className="tool-ui-header"><h1 id="tool-product-title">{localized(schema.product.title, language)}</h1>
      <p>{localized(schema.product.description, language)}</p></header>
    <form className="tool-ui-layout" onSubmit={submit} style={{
      "--tool-ui-input": `${schema.page.input_width ?? 5}fr`,
      "--tool-ui-result": `${schema.page.result_width ?? 7}fr`,
    } as CSSProperties}>
      <div className="tool-ui-input">
        <ToolInputRenderer schema={schema} form={effectiveForm} onChange={onChange} />
        <div className="tool-ui-actions">{schema.actions.map((action) => conditionMatches(action.visible_when, document) &&
          <button className="mono-button primary" type="submit" name="tool-action" value={action.id} key={action.id}>{localized(action.label, language)}</button>)}</div>
      </div>
      <div className="tool-ui-output">
        {status && ["queued", "running", "cancelling"].includes(status) && <div className="tool-ui-run-status" role="status">{language === "en" ? status[0].toUpperCase() + status.slice(1) : ({ queued: "排队中", running: "运行中", cancelling: "正在取消" } as Record<string, string>)[status]} · {run?.progress ?? 0}%</div>}
        {status && ["failed", "cancelled"].includes(status) && <div className="tool-ui-error" role="alert">{language === "en" ? `Run ${status}` : status === "failed" ? "运行失败" : "运行已取消"}</div>}
        {run ? <ToolResultRenderer schema={schema} form={effectiveForm} run={run} events={events} theme={theme} />
          : <div className="tool-ui-placeholder">{language === "en" ? "Run the tool to see results" : "运行工具后在这里查看结果"}</div>}
      </div>
    </form>
  </article>;
}
