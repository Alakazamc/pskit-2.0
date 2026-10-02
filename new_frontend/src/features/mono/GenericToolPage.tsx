import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Database } from "lucide-react";
import { useRef, useState } from "react";
import type { McpTool, ResearchApi } from "../../api/types";
import { useLanguage } from "../../i18n/LanguageProvider";
import { errorTranslationKey } from "../../i18n/errors";

type PropertySchema = { type?: string; description?: string };

function argumentsFromFields(tool: McpTool, values: Record<string, string>): Record<string, unknown> {
  const properties = (tool.input_schema.properties ?? {}) as Record<string, PropertySchema>;
  const arguments_: Record<string, unknown> = {};
  for (const [name, schema] of Object.entries(properties)) {
    const raw = values[name]?.trim();
    if (!raw) continue;
    if (schema.type === "integer" || schema.type === "number") {
      const number = Number(raw);
      if (!Number.isFinite(number) || (schema.type === "integer" && !Number.isInteger(number))) {
        throw new Error("invalid number");
      }
      arguments_[name] = number;
    } else if (schema.type === "boolean") {
      arguments_[name] = raw === "true";
    } else if (schema.type === "array" || schema.type === "object") {
      arguments_[name] = JSON.parse(raw);
    } else {
      arguments_[name] = raw;
    }
  }
  return arguments_;
}

export function GenericToolPage({ api, userId, name }: { api: ResearchApi; userId: string; name: string }) {
  const { t } = useLanguage();
  const queryClient = useQueryClient();
  const catalog = useQuery({ queryKey: ["mcp-tools"], queryFn: api.getMcpTools });
  const tool = catalog.data?.find((item) => item.name === name);
  const properties = (tool?.input_schema.properties ?? {}) as Record<string, PropertySchema>;
  const required = new Set(Array.isArray(tool?.input_schema.required) ? tool.input_schema.required : []);
  const [values, setValues] = useState<Record<string, string>>({});
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pendingInvocation = useRef<{ signature: string; key: string } | null>(null);
  const run = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!tool || busy) return;
    setBusy(true); setError(""); setResult(null);
    try {
      const arguments_ = argumentsFromFields(tool, values);
      const signature = `${tool.name}\0${JSON.stringify(arguments_)}`;
      const key = pendingInvocation.current?.signature === signature
        ? pendingInvocation.current.key : crypto.randomUUID();
      pendingInvocation.current = { signature, key };
      const response = await api.invokeMcpTool(tool.name, arguments_, key);
      pendingInvocation.current = null;
      setResult(response.result);
      await queryClient.invalidateQueries({ queryKey: ["tool-runs", userId] });
    } catch (caught) {
      setError(t(errorTranslationKey(caught) ?? "tools.invokeFailed"));
    } finally {
      setBusy(false);
    }
  };
  return <div className="mono-page-scroll"><div className="mono-page-content">
    <div className="mono-tool-heading"><h1>{name}</h1><p>{tool?.description ?? t("tools.loading")}</p></div>
    {catalog.isError && <p className="mono-form-error" role="alert">{t("tools.loadFailed")}</p>}
    {!catalog.isLoading && !catalog.isError && !tool && <p className="mono-form-error" role="alert">{t("tools.notFound")}</p>}
    {tool && <div className="mono-tool-layout"><section className="mono-panel"><div className="mono-panel-heading"><div><Database size={18} /><h2>{t("tools.arguments")}</h2></div></div>
      <form className="mono-form" onSubmit={(event) => void run(event)}>{Object.entries(properties).map(([key, schema]) => <label key={key}>{key}
        {schema.type === "boolean" ? <select aria-label={key} value={values[key] ?? ""} required={required.has(key)} onChange={(event) => setValues({ ...values, [key]: event.target.value })}><option value="">—</option><option value="true">true</option><option value="false">false</option></select>
          : <input aria-label={key} value={values[key] ?? ""} required={required.has(key)} type={schema.type === "integer" || schema.type === "number" ? "number" : "text"} step={schema.type === "integer" ? "1" : "any"} placeholder={schema.description} onChange={(event) => setValues({ ...values, [key]: event.target.value })} />}
      </label>)}<button className="mono-button primary" type="submit" disabled={busy}>{t("tools.run")} <ArrowRight size={16} /></button>{error && <p className="mono-form-error" role="alert">{error}</p>}</form>
    </section><section className="mono-panel mono-result-panel"><div className="mono-panel-heading"><div><Database size={18} /><h2>{t("tools.result")}</h2></div></div>{result ? <details className="mono-raw-result" open><summary>{t("tools.rawResult")}</summary><pre>{JSON.stringify(result, null, 2)}</pre></details> : <p>{t("tools.noResult")}</p>}</section></div>}
  </div></div>;
}
