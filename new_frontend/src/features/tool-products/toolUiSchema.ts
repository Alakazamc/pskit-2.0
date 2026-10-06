import type {
  LocalizedText,
  ToolRunEvent,
  ToolRunSnapshot,
  ToolUiActionOutput,
  ToolUiConditionOutput,
  ToolUiFieldOutput,
  ToolUiResultViewOutput,
  ToolUiSchemaOutput,
} from "../../api/generated";
import type { Language } from "../../i18n/LanguageProvider";

export type ToolUiSchema = ToolUiSchemaOutput;
export type ToolUiField = ToolUiFieldOutput;
export type ToolUiCondition = ToolUiConditionOutput;
export type ToolUiResultView = ToolUiResultViewOutput;
export type ToolUiAction = ToolUiActionOutput;
export type ToolRun = ToolRunSnapshot;
export type ToolEvent = ToolRunEvent;
export type ToolForm = Record<string, unknown>;

const blockedSegments = new Set(["__proto__", "prototype", "constructor"]);

function pointerSegments(pointer: string): string[] | null {
  if (pointer === "") return [];
  if (!pointer.startsWith("/") || /~(?![01])/u.test(pointer)) return null;
  const segments = pointer.slice(1).split("/").map((part) => part.replaceAll("~1", "/").replaceAll("~0", "~"));
  return segments.some((part) => blockedSegments.has(part)) ? null : segments;
}

export function resolvePointer(document: unknown, pointer: string): unknown {
  const segments = pointerSegments(pointer);
  if (!segments) return undefined;
  let value = document;
  for (const segment of segments) {
    if ((typeof value !== "object" || value === null) || !Object.prototype.hasOwnProperty.call(value, segment)) return undefined;
    value = (value as Record<string, unknown>)[segment];
  }
  return value;
}

function setNested(document: unknown, segments: string[], value: unknown): unknown {
  if (!segments.length) return value;
  const [head, ...tail] = segments;
  const source = typeof document === "object" && document !== null ? document as Record<string, unknown> : {};
  return { ...source, [head]: setNested(source[head], tail, value) };
}

export function setFormPointer(form: ToolForm, pointer: string, value: unknown): ToolForm {
  const segments = pointerSegments(pointer);
  if (!segments || segments[0] !== "form" || segments.length < 2) return form;
  return setNested(form, segments.slice(1), value) as ToolForm;
}

export function localized(value: LocalizedText | null | undefined, language: Language): string {
  if (!value) return "";
  return language === "en" ? value.en : value["zh-CN"];
}

export function conditionMatches(condition: ToolUiCondition | null | undefined, document: unknown): boolean {
  if (!condition) return true;
  if (condition.and) return condition.and.every((item) => conditionMatches(item, document));
  if (condition.or) return condition.or.some((item) => conditionMatches(item, document));
  if (condition.not) return !conditionMatches(condition.not, document);
  const value = condition.source ? resolvePointer(document, condition.source) : undefined;
  if (Object.prototype.hasOwnProperty.call(condition, "equals")) return Object.is(value, condition.equals);
  if (condition.in) return condition.in.some((item) => Object.is(value, item));
  if (condition.exists !== undefined && condition.exists !== null) return condition.exists === (value !== undefined && value !== null);
  return false;
}

function fieldsOf(schema: ToolUiSchema): ToolUiField[] {
  const flattened: ToolUiField[] = [];
  const visit = (fields: ToolUiField[]) => fields.forEach((field) => {
    flattened.push(field);
    visit(field.fields ?? []);
  });
  (schema.sections ?? []).forEach((section) => visit(section.fields));
  return flattened;
}

export function formWithDefaults(schema: ToolUiSchema, form: ToolForm): ToolForm {
  let next = form;
  for (const [name, state] of Object.entries(schema.state ?? {})) {
    if (resolvePointer({ form: next }, `/form/${name}`) === undefined && Object.prototype.hasOwnProperty.call(state, "initial")) {
      next = setFormPointer(next, `/form/${name}`, state.initial);
    }
  }
  for (const field of fieldsOf(schema)) {
    if (!field.input_pointer || field.default === undefined || field.default === null) continue;
    if (resolvePointer({ form: next }, field.input_pointer) === undefined) next = setFormPointer(next, field.input_pointer, field.default);
  }
  return next;
}

export function resolveActionTarget(action: ToolUiAction, document: unknown): string | undefined {
  if ("action_id" in action.target) return action.target.action_id;
  const selection = action.target.by_state;
  const state = resolvePointer(document, selection.source);
  return selection.map[String(state)];
}

export function projectActionArguments(
  actions: Array<{ id: string; input_schema: Record<string, unknown> }>,
  actionId: string,
  form: ToolForm,
): ToolForm {
  const schema = actions.find((action) => action.id === actionId)?.input_schema;
  const properties = schema?.properties;
  if (typeof properties !== "object" || properties === null || Array.isArray(properties)) return form;
  return Object.fromEntries(
    Object.keys(properties).filter((key) => Object.prototype.hasOwnProperty.call(form, key)).map((key) => [key, form[key]]),
  );
}

export function applyTransforms(value: unknown, transforms: ToolUiResultView["transforms"]): unknown {
  let result = value;
  for (const transform of transforms ?? []) {
    if (transform.name === "identity") continue;
    if (transform.name === "limit" && Array.isArray(result)) result = result.slice(0, transform.limit ?? result.length);
    if (transform.name === "sort" && Array.isArray(result) && transform.field) {
      const direction = transform.direction === "desc" ? -1 : 1;
      result = [...result].sort((left, right) => {
        const a = typeof left === "object" && left !== null ? (left as Record<string, unknown>)[transform.field!] : undefined;
        const b = typeof right === "object" && right !== null ? (right as Record<string, unknown>)[transform.field!] : undefined;
        return String(a ?? "").localeCompare(String(b ?? ""), undefined, { numeric: true }) * direction;
      });
    }
    if (transform.name === "number-format" && typeof result === "number") result = result.toFixed(transform.digits ?? 2);
    if (transform.name === "rename-fields" && Array.isArray(result)) result = result.map((row) => {
      if (typeof row !== "object" || row === null || Array.isArray(row)) return row;
      return Object.fromEntries(Object.entries(row).map(([key, item]) => [transform.renames?.[key] ?? key, item]));
    });
  }
  return result;
}
