import type { ChangeEvent } from "react";
import { useMemo, useState } from "react";
import type {
  AcceptanceSuite, DiscoverySnapshot, ProbeSnapshot, PublishedToolProduct,
  QualificationReport, ToolProductDraft,
} from "../../api/admin";
import type { DiscoveredTool, ToolUiFieldOutput } from "../../api/generated/types.gen";
import { useLanguage } from "../../i18n/LanguageProvider";
import { adminErrorKey, useAdmin, useAdminList } from "./adminQueries";
import { ToolProductBuilder } from "./ToolProductBuilder";

const localized = (value: string) => ({ en: value, "zh-CN": value });
const identifier = (value: string) => value.toLowerCase().replace(/[^a-z0-9_.-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 80) || "tool";
const titleFromName = (value: string) => value.split(/[_\-.]+/).map((part, index) => part.toLowerCase() === "rna" ? "RNA" : index ? part.toLowerCase() : part[0]?.toUpperCase() + part.slice(1).toLowerCase()).join(" ");
const object = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};

function inputField(name: string, schema: unknown, required: boolean): ToolUiFieldOutput {
  const property = object(schema);
  const enumValues = Array.isArray(property.enum) ? property.enum : [];
  const component: ToolUiFieldOutput["component"] = name.toLowerCase().includes("protein") ? "protein-input"
    : name.toLowerCase().includes("sequence") ? "sequence-input"
      : property.type === "number" || property.type === "integer" ? "number-input"
        : enumValues.length ? "select" : "text-input";
  return {
    id: identifier(name), component, label: localized(name), input_pointer: `/form/${name}`,
    required, options: enumValues.map((value) => ({ value: value as string | number | boolean, label: localized(String(value)) })),
    minimum: typeof property.minimum === "number" ? property.minimum : null,
    maximum: typeof property.maximum === "number" ? property.maximum : null,
  };
}

function exampleArguments(tool: DiscoveredTool) {
  const schema = object(tool.input_schema); const properties = object(schema.properties);
  return Object.fromEntries(Object.entries(properties).map(([name, raw]) => {
    const property = object(raw); const value = Array.isArray(property.enum) ? property.enum[0]
      : property.type === "number" || property.type === "integer" ? 1
        : name.toLowerCase().includes("protein") ? "MKT" : "example";
    return [name, value];
  }));
}

function productFromTool(tool: DiscoveredTool, serviceId: string, serviceRevision: number, owner: string): { draft: ToolProductDraft; suite: AcceptanceSuite } {
  const serviceSlug = identifier(serviceId).replaceAll("_", "-");
  const toolSlug = identifier(tool.name).replaceAll("_", "-");
  const slug = toolSlug.startsWith(`${serviceSlug}-`) ? toolSlug : `${serviceSlug}-${toolSlug}`;
  const productId = `product-${slug}`; const actionId = identifier(tool.name); const bindingId = `binding-${actionId}`;
  const title = localized(titleFromName(tool.name)); const description = localized(tool.description || title.en);
  const inputSchema = object(tool.input_schema); const properties = object(inputSchema.properties); const required = new Set(Array.isArray(inputSchema.required) ? inputSchema.required.map(String) : []);
  const fields = Object.entries(properties).map(([name, schema]) => inputField(name, schema, required.has(name)));
  if (!fields.length) fields.push(inputField("input", { type: "string" }, true));
  const uiSchema = {
    schema_version: "pskit.tool-ui.v1" as const, product: { slug, title, description }, page: { layout: "split-workspace" as const, input_width: 5, result_width: 7 }, state: {},
    sections: [{ id: "inputs", title: localized("Inputs"), fields }],
    actions: [{ id: "run", label: localized("Run"), kind: "start_run" as const, target: { action_id: actionId } }],
    result_views: [{ id: "result", component: "json-inspector" as const, source: "/run/result", title: localized("Result"), preview_limit: 100 }], handoffs: [],
  };
  const draft: ToolProductDraft = {
    product_id: productId, slug, revision: 1, owner_user_id: owner, title, description, ui_schema: uiSchema,
    bindings: [{ binding_id: bindingId, product_action_id: actionId, service_id: serviceId, service_revision: serviceRevision, capability_id: `${identifier(serviceId)}.${actionId}`, capability_version: String(serviceRevision), adapter: "immediate_mcp", submit_tool: tool.name, status_tool: null, cancel_tool: null, remote_output_schema: tool.remote_output_schema, result_schema: tool.remote_output_schema, result_mapping: { kind: "json_pointer", pointer: "", renames: {}, transforms: [] }, required_usage: ["wall_ms", "gpu_device_ms"], cancellation: "cooperative" }],
    actions: [{ id: actionId, label: title, description, kind: "capability", binding_ids: [bindingId], input_schema: tool.input_schema }],
    visibility: { audience: "members", allowed_user_ids: [] }, acceptance_suite_id: `${slug}-acceptance`, acceptance_suite_revision: 1, handoffs: [],
  };
  const outputProperties = object(object(tool.remote_output_schema).properties);
  const firstArray = Object.entries(outputProperties).find(([, value]) => object(value).type === "array")?.[0];
  const firstOutput = firstArray ?? Object.keys(outputProperties)[0] ?? "result";
  const suite: AcceptanceSuite = { suite_id: draft.acceptance_suite_id, revision: 1, cases: [{ case_id: "smoke", action_id: actionId, arguments: exampleArguments(tool), invalid_arguments: [{}], result_assertions: [{ pointer: `/${firstOutput}`, predicate: firstArray ? "min_items" : "exists", value: firstArray ? 1 : true }], required_progress_types: [], check_idempotency: true, check_cancellation: false }] };
  return { draft, suite };
}

function digest(value: unknown) { return JSON.stringify(value); }

function Evidence({ report }: { report: QualificationReport }) {
  const { t } = useLanguage();
  const protocol = report.cases.flatMap((item) => Object.entries(item.protocol_assertions ?? {}).map(([name, passed]) => ({ id: `${item.case_id}-${name}`, name, passed })));
  const science = report.cases.flatMap((item) => Object.entries(item.scientific_assertions ?? {}).map(([name, passed]) => ({ id: `${item.case_id}-${name}`, name, passed })));
  return <section className="admin-product-evidence"><div><h2>{t("admin.toolProducts.protocolEvidence")}</h2><p className="admin-state">{report.protocol_passed ? t("admin.toolProducts.passed") : t("admin.toolProducts.failed")}</p><ul>{protocol.map((item) => <li key={item.id} data-passed={item.passed}>{item.passed ? "✓" : "×"} {item.name}</li>)}</ul></div><div><h2>{t("admin.toolProducts.scientificEvidence")}</h2><p className="admin-state">{report.scientific_passed ? t("admin.toolProducts.passed") : t("admin.toolProducts.failed")}</p><ul>{science.map((item) => <li key={item.id} data-passed={item.passed}>{item.passed ? "✓" : "×"} {item.name}</li>)}</ul></div><div className="admin-product-usage"><strong>{t("admin.toolProducts.usageSource")}</strong>{report.cases.map((item) => <p key={item.case_id}>{item.usage_source ?? t("admin.source.unknown")} · {item.message || t("admin.toolProducts.usageInEvidence")}</p>)}</div></section>;
}

export function AdminToolProductsPage({ theme }: { theme: "dark" | "light" }) {
  const { api, me } = useAdmin(); const { t } = useLanguage();
  const services = useAdminList("services", "services:read", api.listServices);
  const rows = services.data?.pages.flatMap((page) => page.items) ?? [];
  const manageable = rows.filter((service) => me.roles.includes("platform_admin") || me.service_ids.includes(service.service_id));
  const canWrite = me.permissions.includes("services:write"); const canPublish = me.permissions.includes("services:publish"); const platform = me.roles.includes("platform_admin");
  const [serviceId, setServiceId] = useState(""); const [reason, setReason] = useState("");
  const [probeForm, setProbeForm] = useState({ service_id: "", uri: "", credential_ref: "", transport: "streamable_http" as const, network_zone: "public" });
  const [probe, setProbe] = useState<ProbeSnapshot | null>(null); const [discovery, setDiscovery] = useState<DiscoverySnapshot | null>(null);
  const [draft, setDraft] = useState<ToolProductDraft | null>(null); const [suite, setSuite] = useState<AcceptanceSuite | null>(null);
  const [savedSignature, setSavedSignature] = useState(""); const [qualifiedSignature, setQualifiedSignature] = useState("");
  const [report, setReport] = useState<QualificationReport | null>(null); const [reportId, setReportId] = useState("");
  const [reviewing, setReviewing] = useState(false); const [release, setRelease] = useState<PublishedToolProduct | null>(null);
  const [busy, setBusy] = useState(""); const [error, setError] = useState("");
  const currentSignature = useMemo(() => draft && suite ? digest({ draft, suite }) : "", [draft, suite]);
  const stale = !!report && qualifiedSignature !== currentSignature;
  const selectedService = manageable.find((service) => service.service_id === serviceId);
  const act = async (name: string, action: () => Promise<void>) => { setBusy(name); setError(""); try { await action(); } catch (caught) { setError(t(adminErrorKey(caught))); } finally { setBusy(""); } };
  const probeEndpoint = () => void act("probe", async () => setProbe(await api.probeMcp({ ...probeForm, credential_ref: probeForm.credential_ref || null })));
  const discover = () => void act("discover", async () => {
    if (!selectedService) return; setDiscovery(await api.discoverMcp(selectedService.service_id, { service_revision: selectedService.revision, reason }));
  });
  const selectTool = (tool: DiscoveredTool) => {
    if (!discovery) return; const next = productFromTool(tool, discovery.service_id, discovery.service_revision, me.user_id);
    setDraft(next.draft); setSuite(next.suite); setSavedSignature(""); setQualifiedSignature(""); setReport(null); setRelease(null); setReviewing(false);
  };
  const save = () => void act("save", async () => {
    if (!draft || !suite) return; const saved = await api.saveToolProductDraft(draft.product_id, { expected_revision: Math.max(0, draft.revision - 1), reason, draft, acceptance_suite: suite });
    setDraft(saved); const signature = digest({ draft: saved, suite }); setSavedSignature(signature); setReport(null); setQualifiedSignature("");
  });
  const qualify = () => void act("qualify", async () => {
    if (!draft) return; const value = await api.qualifyToolProduct(draft.product_id, { revision: draft.revision, suite_revision: draft.acceptance_suite_revision, reason });
    setReport(value); setReportId(value.report_id); setQualifiedSignature(currentSignature); setReviewing(false);
  });
  const loadEvidence = () => void act("evidence", async () => { const value = await api.getToolProductQualification(reportId); setReport(value); setQualifiedSignature(currentSignature); });
  const publish = () => void act("publish", async () => {
    if (!report) return; setRelease(await api.publishToolProduct(report.report_id, { product_id: report.product_id, revision: report.product_revision, reason })); setReviewing(false);
  });
  const releaseAction = (kind: "suspend" | "rollback") => void act(kind, async () => {
    if (!release) return; setRelease(kind === "suspend" ? await api.suspendToolProduct(release.release_id, { reason }) : await api.rollbackToolProduct(release.release_id, { reason }));
  });
  const reasonReady = reason.trim().length >= 5;
  const chooseService = (event: ChangeEvent<HTMLSelectElement>) => setServiceId(event.target.value);
  return <div className="admin-product-page">
    {error && <p role="alert" className="admin-error">{error}</p>}
    <section className="admin-product-evidence-loader admin-form"><h2>{t("admin.toolProducts.evidenceLookup")}</h2><div className="admin-inline-form"><label>{t("admin.toolProducts.reportId")}<input value={reportId} onChange={(event) => setReportId(event.target.value)} /></label>{canPublish && !canWrite && <label>{t("admin.reason")}<input value={reason} onChange={(event) => setReason(event.target.value)} /></label>}<button type="button" disabled={!reportId || !!busy} onClick={loadEvidence}>{t("admin.toolProducts.loadEvidence")}</button></div></section>
    {platform && canWrite && <section className="admin-product-step admin-form"><div className="admin-step-number">1</div><div><h2>{t("admin.toolProducts.connect")}</h2><div className="admin-builder-grid"><label>{t("admin.toolProducts.serviceUrl")}<input value={probeForm.uri} onChange={(event) => setProbeForm({ ...probeForm, uri: event.target.value })} /></label><label>{t("admin.toolProducts.serviceId")}<input value={probeForm.service_id} onChange={(event) => setProbeForm({ ...probeForm, service_id: event.target.value })} /></label><label>{t("admin.toolProducts.credentialRef")}<input value={probeForm.credential_ref} onChange={(event) => setProbeForm({ ...probeForm, credential_ref: event.target.value })} /></label><label>{t("admin.toolProducts.networkZone")}<input value={probeForm.network_zone} onChange={(event) => setProbeForm({ ...probeForm, network_zone: event.target.value })} /></label></div><button type="button" disabled={!probeForm.uri || !probeForm.service_id || !!busy} onClick={probeEndpoint}>{t("admin.toolProducts.probe")}</button>{probe && <p role="status" className="admin-check-result">{t("admin.toolProducts.endpointApproved")} · {probe.endpoint_id}</p>}</div></section>}
    {canWrite && <section className="admin-product-step admin-form"><div className="admin-step-number">2</div><div><h2>{t("admin.toolProducts.discover")}</h2><div className="admin-inline-form"><label>{t("admin.toolProducts.approvedService")}<select value={serviceId} onChange={chooseService}><option value="">—</option>{manageable.map((service) => <option value={service.service_id} key={service.service_id}>{service.name}</option>)}</select></label><label>{t("admin.reason")}<input value={reason} onChange={(event) => setReason(event.target.value)} /></label><button type="button" disabled={!selectedService || !reasonReady || !!busy} onClick={discover}>{t("admin.toolProducts.discoverCapabilities")}</button></div>{discovery && <div className="admin-discovered-tools">{discovery.tools.map((tool) => <article key={tool.name}><h3>{tool.name}</h3><p>{tool.description}</p><button type="button" onClick={() => selectTool(tool)}>{t("admin.toolProducts.useTool", { name: tool.name })}</button></article>)}</div>}</div></section>}
    {draft && suite && canWrite && <section className="admin-product-step"><div className="admin-step-number">3</div><div><h2>{t("admin.toolProducts.build")}</h2><ToolProductBuilder draft={draft} suite={suite} theme={theme} onDraftChange={setDraft} onSuiteChange={setSuite} /><div className="admin-actions admin-product-primary-actions"><button type="button" disabled={!reasonReady || !!busy} onClick={save}>{t("admin.toolProducts.saveDraft")}</button><button type="button" disabled={!savedSignature || savedSignature !== currentSignature || !!busy} onClick={qualify}>{t("admin.toolProducts.qualify")}</button></div></div></section>}
    {report && <><Evidence report={report} />{stale && <p role="alert" className="admin-error">{t("admin.toolProducts.stale")}</p>}{report.status === "failed" && <p role="alert" className="admin-error">{t("admin.toolProducts.failedBlocksPublish")}</p>}{canPublish && report.status === "passed" && !stale && <button type="button" disabled={!reasonReady || !!busy} onClick={() => setReviewing(true)}>{t("admin.toolProducts.reviewRelease")}</button>}</>}
    {reviewing && report && <section className="admin-release-diff" role="region" aria-label={t("admin.toolProducts.releaseDiff")}><h2>{t("admin.toolProducts.releaseDiff")}</h2><p>{t("admin.toolProducts.revision", { revision: report.product_revision })}</p><dl><dt>Binding</dt><dd>{report.binding_digest}</dd><dt>UI</dt><dd>{report.ui_digest}</dd><dt>Suite</dt><dd>{report.suite_digest}</dd></dl><button type="button" disabled={!!busy} onClick={publish}>{t("admin.toolProducts.publish")}</button></section>}
    {release && <section className="admin-product-release"><p role="status">{t("admin.toolProducts.published", { id: release.release_id })}</p>{canPublish && <div className="admin-actions"><button type="button" disabled={!!busy || release.state === "suspended"} onClick={() => releaseAction("suspend")}>{t("admin.toolProducts.suspend")}</button><button type="button" disabled={!!busy || release.state === "published"} onClick={() => releaseAction("rollback")}>{t("admin.toolProducts.rollback")}</button></div>}</section>}
  </div>;
}
