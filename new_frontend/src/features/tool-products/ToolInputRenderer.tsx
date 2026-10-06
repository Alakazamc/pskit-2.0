import type { ChangeEvent, ReactNode } from "react";
import { useLanguage } from "../../i18n/LanguageProvider";
import {
  conditionMatches,
  localized,
  resolvePointer,
  setFormPointer,
  type ToolForm,
  type ToolUiField,
  type ToolUiSchema,
} from "./toolUiSchema";

const supported = new Set([
  "text-input", "number-input", "textarea", "select", "segmented-control", "checkbox", "switch",
  "file-upload", "protein-input", "sequence-input", "parameter-group", "advanced-section",
]);

function FieldShell({ field, children }: { field: ToolUiField; children: ReactNode }) {
  const { language } = useLanguage();
  return <div className={`tool-ui-field tool-ui-field-${field.component}`}>
    {children}
    {field.help && <small id={`${field.id}-help`}>{localized(field.help, language)}</small>}
  </div>;
}

function ScalarField({ field, form, update }: { field: ToolUiField; form: ToolForm; update: (field: ToolUiField, value: unknown) => void }) {
  const { language } = useLanguage();
  const label = localized(field.label, language);
  const value = field.input_pointer ? resolvePointer({ form }, field.input_pointer) : undefined;
  const describedBy = field.help ? `${field.id}-help` : undefined;
  if (field.component === "textarea" || field.component === "sequence-input") return <FieldShell field={field}>
    <label htmlFor={field.id}>{label}</label>
    <textarea id={field.id} aria-describedby={describedBy} required={field.required} value={typeof value === "string" ? value : ""}
      spellCheck={field.component !== "sequence-input"} onChange={(event) => update(field, event.target.value)} />
  </FieldShell>;
  if (field.component === "text-input" || field.component === "protein-input") return <FieldShell field={field}>
    <label htmlFor={field.id}>{label}</label>
    <input id={field.id} aria-describedby={describedBy} required={field.required} value={typeof value === "string" ? value : ""}
      autoComplete="off" spellCheck={false} onChange={(event) => update(field, event.target.value)} />
  </FieldShell>;
  if (field.component === "number-input") return <FieldShell field={field}>
    <label htmlFor={field.id}>{label}</label>
    <input id={field.id} aria-describedby={describedBy} type="number" required={field.required} min={field.minimum ?? undefined} max={field.maximum ?? undefined}
      step={field.step ?? "any"} value={typeof value === "number" || typeof value === "string" ? value : ""}
      onChange={(event) => update(field, event.target.value === "" ? undefined : Number(event.target.value))} />
  </FieldShell>;
  if (field.component === "select") return <FieldShell field={field}>
    <label htmlFor={field.id}>{label}</label>
    <select id={field.id} aria-describedby={describedBy} required={field.required} value={String(value ?? "")}
      onChange={(event) => update(field, field.options?.find((option) => String(option.value) === event.target.value)?.value)}>
      {!field.required && <option value="" />}
      {(field.options ?? []).map((option) => <option key={String(option.value)} value={String(option.value)}>{localized(option.label, language)}</option>)}
    </select>
  </FieldShell>;
  if (field.component === "segmented-control") return <FieldShell field={field}>
    <span className="tool-ui-field-label">{label}</span>
    <div className="tool-ui-segments" role="radiogroup" aria-label={label}>
      {(field.options ?? []).map((option) => <button type="button" role="radio" aria-checked={Object.is(value, option.value)} key={String(option.value)}
        onClick={() => update(field, option.value)}>{localized(option.label, language)}</button>)}
    </div>
  </FieldShell>;
  if (field.component === "checkbox") return <FieldShell field={field}>
    <label className="tool-ui-toggle"><input id={field.id} type="checkbox" required={field.required} checked={value === true}
      onChange={(event) => update(field, event.target.checked)} /><span>{label}</span></label>
  </FieldShell>;
  if (field.component === "switch") return <FieldShell field={field}>
    <button id={field.id} className="tool-ui-switch" type="button" role="switch" aria-checked={value === true}
      onClick={() => update(field, value !== true)}><span aria-hidden="true" /><span>{label}</span></button>
  </FieldShell>;
  if (field.component === "file-upload") {
    const changeFiles = (event: ChangeEvent<HTMLInputElement>) => update(field, Array.from(event.target.files ?? []).slice(0, field.max_files ?? 100));
    return <FieldShell field={field}><label htmlFor={field.id}>{label}</label><input id={field.id} type="file" multiple={(field.max_files ?? 1) > 1}
      required={field.required} accept={(field.accepted_types ?? []).join(",")} onChange={changeFiles} /></FieldShell>;
  }
  return <div role="alert" className="tool-ui-error">Unsupported component: {field.component}</div>;
}

function RenderField({ field, form, onChange }: { field: ToolUiField; form: ToolForm; onChange: (form: ToolForm) => void }) {
  const { language } = useLanguage();
  const document = { form };
  if (!conditionMatches(field.visible_when, document)) return null;
  if (!supported.has(field.component)) return <div role="alert" className="tool-ui-error">Unsupported component: {field.component}</div>;
  const update = (changed: ToolUiField, value: unknown) => {
    if (changed.input_pointer) onChange(setFormPointer(form, changed.input_pointer, value));
  };
  if (field.component === "parameter-group") return <fieldset className="tool-ui-group"><legend>{localized(field.label, language)}</legend>
    {(field.fields ?? []).map((child) => <RenderField key={child.id} field={child} form={form} onChange={onChange} />)}
  </fieldset>;
  if (field.component === "advanced-section") return <details className="tool-ui-advanced"><summary>{localized(field.label, language)}</summary>
    <div>{(field.fields ?? []).map((child) => <RenderField key={child.id} field={child} form={form} onChange={onChange} />)}</div>
  </details>;
  return <ScalarField field={field} form={form} update={update} />;
}

export function ToolInputRenderer({ schema, form, onChange }: { schema: ToolUiSchema; form: ToolForm; onChange: (form: ToolForm) => void }) {
  const { language } = useLanguage();
  const document = { form };
  return <div className="tool-ui-input-sections">{(schema.sections ?? []).map((section) => conditionMatches(section.visible_when, document) &&
    <section className="tool-ui-section" key={section.id} aria-labelledby={`${section.id}-title`}>
      <h2 id={`${section.id}-title`}>{localized(section.title, language)}</h2>
      {section.description && <p>{localized(section.description, language)}</p>}
      {section.fields.map((field) => <RenderField key={field.id} field={field} form={form} onChange={onChange} />)}
    </section>)}</div>;
}
