import { useEffect, useMemo, useState } from "react";
import type { AcceptanceSuite, ToolProductDraft } from "../../api/admin";
import { useLanguage } from "../../i18n/LanguageProvider";
import { ToolUiRenderer } from "../tool-products/ToolUiRenderer";
import type { ToolForm, ToolUiSchema } from "../tool-products/toolUiSchema";

const fieldComponents = ["text-input", "number-input", "textarea", "select", "segmented-control", "checkbox", "switch", "file-upload", "protein-input", "sequence-input"] as const;
const resultComponents = ["metric-grid", "stage-flow", "sequence-table", "data-table", "line-chart", "scatter-plot", "heatmap", "structure-viewer", "artifact-list", "json-inspector"] as const;

export function ToolProductBuilder({ draft, suite, theme, onDraftChange, onSuiteChange }: {
  draft: ToolProductDraft;
  suite: AcceptanceSuite;
  theme: "dark" | "light";
  onDraftChange: (draft: ToolProductDraft) => void;
  onSuiteChange: (suite: AcceptanceSuite) => void;
}) {
  const { language, t } = useLanguage();
  const [form, setForm] = useState<ToolForm>({});
  const [advanced, setAdvanced] = useState(() => JSON.stringify({ draft, acceptance_suite: suite }, null, 2));
  const [advancedError, setAdvancedError] = useState("");
  useEffect(() => setAdvanced(JSON.stringify({ draft, acceptance_suite: suite }, null, 2)), [draft, suite]);
  const sections = draft.ui_schema.sections ?? [];
  const resultViews = draft.ui_schema.result_views ?? [];
  const firstField = sections[0]?.fields?.[0];
  const firstResult = resultViews[0];
  const title = language === "en" ? draft.title.en : draft.title["zh-CN"];
  const updateDraft = (next: Partial<ToolProductDraft>) => onDraftChange({ ...draft, ...next });
  const updateUi = (ui: Partial<ToolUiSchema>) => updateDraft({ ui_schema: { ...draft.ui_schema, ...ui } as ToolProductDraft["ui_schema"] });
  const updateFirstField = (key: "component" | "input_pointer", value: string) => {
    if (!firstField) return;
    const nextSections = sections.map((section, sectionIndex) => sectionIndex ? section : { ...section, fields: (section.fields ?? []).map((field, fieldIndex) => fieldIndex ? field : { ...field, [key]: value }) });
    updateUi({ sections: nextSections });
  };
  const updateFirstResult = (key: "component" | "source", value: string) => {
    if (!firstResult) return;
    updateUi({ result_views: resultViews.map((view, index) => index ? view : { ...view, [key]: value }) });
  };
  const previewSchema = useMemo(() => draft.ui_schema as ToolUiSchema, [draft.ui_schema]);
  const applyAdvanced = () => {
    try {
      const parsed = JSON.parse(advanced) as { draft?: ToolProductDraft; acceptance_suite?: AcceptanceSuite };
      if (!parsed.draft || !parsed.acceptance_suite) throw new Error("missing roots");
      onDraftChange(parsed.draft); onSuiteChange(parsed.acceptance_suite); setAdvancedError("");
    } catch { setAdvancedError(t("admin.toolProducts.invalidImport")); }
  };
  return <section className="admin-product-builder" aria-label={t("admin.toolProducts.builder")}>
    <div className="admin-product-builder-fields admin-form">
      <h2>{t("admin.toolProducts.builder")}</h2>
      <div className="admin-builder-grid">
        <label>{t("admin.toolProducts.productId")}<input value={draft.product_id} onChange={(event) => updateDraft({ product_id: event.target.value })} /></label>
        <label>{t("admin.toolProducts.slug")}<input value={draft.slug} onChange={(event) => {
          const slug = event.target.value; updateDraft({ slug, ui_schema: { ...draft.ui_schema, product: { ...draft.ui_schema.product, slug } } });
        }} /></label>
        <label>{t("admin.toolProducts.titleEn")}<input value={draft.title.en} onChange={(event) => {
          const titleValue = { ...draft.title, en: event.target.value }; updateDraft({ title: titleValue, ui_schema: { ...draft.ui_schema, product: { ...draft.ui_schema.product, title: titleValue } } });
        }} /></label>
        <label>{t("admin.toolProducts.titleZh")}<input value={draft.title["zh-CN"]} onChange={(event) => {
          const titleValue = { ...draft.title, "zh-CN": event.target.value }; updateDraft({ title: titleValue, ui_schema: { ...draft.ui_schema, product: { ...draft.ui_schema.product, title: titleValue } } });
        }} /></label>
        <label>{t("admin.toolProducts.layout")}<select value={draft.ui_schema.page.layout} onChange={(event) => updateUi({ page: { ...draft.ui_schema.page, layout: event.target.value as "split-workspace" | "single-column", input_width: event.target.value === "split-workspace" ? 5 : 5, result_width: event.target.value === "split-workspace" ? 7 : 7 } })}><option value="split-workspace">split-workspace</option><option value="single-column">single-column</option></select></label>
        {firstField && <><label>{t("admin.toolProducts.fieldComponent")}<select value={firstField.component} onChange={(event) => updateFirstField("component", event.target.value)}>{fieldComponents.map((component) => <option key={component}>{component}</option>)}</select></label><label>{t("admin.toolProducts.inputPointer")}<input value={firstField.input_pointer ?? ""} onChange={(event) => updateFirstField("input_pointer", event.target.value)} /></label></>}
        {firstResult && <><label>{t("admin.toolProducts.resultComponent")}<select value={firstResult.component} onChange={(event) => updateFirstResult("component", event.target.value)}>{resultComponents.map((component) => <option key={component}>{component}</option>)}</select></label><label>{t("admin.toolProducts.resultPointer")}<input value={firstResult.source} onChange={(event) => updateFirstResult("source", event.target.value)} /></label></>}
      </div>
      <details className="admin-product-advanced"><summary>{t("admin.toolProducts.advanced")}</summary><p className="admin-hint">{t("admin.toolProducts.yamlHint")}</p><textarea className="admin-json" aria-label={t("admin.toolProducts.importExport")} value={advanced} onChange={(event) => setAdvanced(event.target.value)} />{advancedError && <p role="alert" className="admin-error">{advancedError}</p>}<div className="admin-actions"><button type="button" onClick={applyAdvanced}>{t("admin.toolProducts.applyImport")}</button><button type="button" onClick={() => void navigator.clipboard?.writeText(advanced)}>{t("admin.toolProducts.copyExport")}</button></div></details>
    </div>
    <section className="admin-product-preview" aria-label={t("admin.toolProducts.preview")}><h2>{title}</h2><ToolUiRenderer schema={previewSchema} form={form} run={null} events={[]} onChange={setForm} onAction={() => undefined} theme={theme} showHeader={false} /></section>
  </section>;
}
