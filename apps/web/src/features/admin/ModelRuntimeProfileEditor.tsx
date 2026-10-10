import type { ModelOption } from "../../api/types";
import type { ModelRuntimeProfile } from "../../api/generated/types.gen";
import { useLanguage } from "../../i18n/LanguageProvider";

export function ModelRuntimeProfileEditor({ profile, alias, revision, gateway, disabled, onChange }: {
  profile: ModelRuntimeProfile | null; alias: string; revision: number; gateway: ModelOption;
  disabled: boolean; onChange: (profile: ModelRuntimeProfile) => void;
}) {
  const { t } = useLanguage();
  if (!profile) return <section className="admin-runtime-profile"><h3>{t("admin.modelRuntime")}</h3>
    <p className="admin-hint">{t("admin.profileRequired")}</p>
    <button type="button" disabled={disabled} onClick={() => onChange({ alias, revision: Math.max(1, revision + 1), protocol: "chat_completions", display_name: gateway.display_name || alias,
      provider_label: gateway.provider_label || null, supports_tools: false, supports_tool_streaming: false, supports_images: false, reasoning_levels: [] })}>{t("admin.declareProfile")}</button>
  </section>;
  const update = (patch: Partial<ModelRuntimeProfile>) => onChange({ ...profile, ...patch });
  const fields = [
    ["supports_tools", "admin.declaredTools"],
    ["supports_tool_streaming", "admin.declaredToolStreaming"],
    ["supports_images", "admin.declaredImages"],
  ] as const;
  return <section className="admin-runtime-profile"><h3>{t("admin.modelRuntime")}</h3>
    <p className="admin-hint">{t("admin.profileDeclarationHint")}</p>
    <label>{t("admin.modelProtocol")}<input readOnly value={profile.protocol ?? "chat_completions"} /></label>
    <label>{t("admin.modelDisplayName")}<input value={profile.display_name ?? ""} maxLength={200} disabled={disabled} onChange={(event) => update({ display_name: event.target.value || null })} /></label>
    <label>{t("admin.modelProviderLabel")}<input value={profile.provider_label ?? ""} maxLength={100} disabled={disabled} onChange={(event) => update({ provider_label: event.target.value || null })} /></label>
    <div className="admin-checkboxes">{fields.map(([field, label]) => <label key={field}><input type="checkbox" checked={profile[field] ?? false} disabled={disabled} onChange={(event) => update({ [field]: event.target.checked })} />{t(label)}</label>)}</div>
    <fieldset disabled={disabled}><legend>{t("admin.declaredReasoning")}</legend><div className="admin-checkboxes">{(["off", "minimal", "low", "medium", "high", "xhigh", "max"] as const).map((level) => <label key={level}>
      <input type="checkbox" checked={profile.reasoning_levels?.includes(level) ?? false} onChange={(event) => {
        const next = event.target.checked ? [...(profile.reasoning_levels ?? []), level] : (profile.reasoning_levels ?? []).filter((value) => value !== level);
        update({ reasoning_levels: next, wire_policy: { ...profile.wire_policy, reasoning_mode: next.length ? "reasoning_effort" : "omit" } });
      }} />{level}
    </label>)}</div></fieldset>
  </section>;
}

export function ModelQualificationStatus({ profile }: { profile?: ModelRuntimeProfile | null }) {
  const { t } = useLanguage();
  const evidence = profile?.qualification_evidence;
  const status = profile?.qualification_status ?? "untested";
  const labels = { untested: "admin.qualificationUntested", passed: "admin.qualificationPassed", failed: "admin.qualificationFailed", degraded: "admin.qualificationDegraded" } as const;
  const incomplete = evidence?.cases.filter((check) => check.status !== "passed") ?? [];
  return <section className="admin-qualification" aria-label={t("admin.modelQualification")}>
    <h3>{t("admin.modelQualification")}</h3><p>{t(labels[status])}</p>
    {evidence && <p className="admin-hint">{t("admin.qualificationScope", { scope: evidence.verification_scope })}</p>}
    {incomplete.length > 0 && <ul>{incomplete.map((check) => <li key={check.case_id}>{check.case_id}: {check.status}</li>)}</ul>}
    <p className="admin-hint">{t("admin.qualificationHint")}</p>
  </section>;
}
