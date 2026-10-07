import { useEffect, useRef, useState } from "react";

function textOf(value: unknown): string {
  return typeof value === "string" ? value : value === undefined ? "" : JSON.stringify(value, null, 2);
}

export function JsonInput({ id, value, required, describedBy, language, onChange }: {
  id: string; value: unknown; required?: boolean; describedBy?: string; language: "en" | "zh";
  onChange: (value: unknown) => void;
}) {
  const [text, setText] = useState(() => textOf(value));
  const emitted = useRef(value);
  useEffect(() => {
    if (!Object.is(emitted.current, value)) {
      emitted.current = value;
      setText(textOf(value));
    }
  }, [value]);
  let invalid = false;
  if (text.trim()) {
    try { JSON.parse(text); } catch { invalid = true; }
  }
  const error = language === "en" ? "Enter valid JSON." : "请输入有效的 JSON。";
  return <textarea id={id} className="tool-ui-json-input" spellCheck={false} required={required}
    aria-describedby={describedBy} aria-invalid={invalid || undefined} value={text}
    ref={(field) => field?.setCustomValidity(invalid ? error : "")}
    onChange={(event) => {
      const next = event.target.value;
      setText(next);
      let parsed: unknown = next.trim() ? next : undefined;
      try { parsed = next.trim() ? JSON.parse(next) : undefined; } catch { /* Keep invalid text in the draft. */ }
      emitted.current = parsed;
      onChange(parsed);
    }} />;
}
