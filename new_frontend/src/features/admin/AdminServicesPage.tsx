import { useState } from "react";
import type { AdminService, ConfigRelease, ServiceCheck, ServiceDraft } from "../../api/admin";
import type { CapabilityVersion } from "../../api/generated-compute";
import { useLanguage } from "../../i18n/LanguageProvider";
import { AdminListFrame, MutationFeedback, ReasonField, StateLabel } from "./AdminControls";
import { useAdmin, useAdminList, useAdminMutation } from "./adminQueries";

function ServiceEditor({ row, updated }: { row: AdminService; updated: (row: AdminService) => void }) {
  const { api, me } = useAdmin();
  const { t } = useLanguage();
  const [id, setId] = useState(row.service_id);
  const [name, setName] = useState(row.name);
  const [owner, setOwner] = useState(row.owner_user_id);
  const [transport, setTransport] = useState(row.transport);
  const [endpoint, setEndpoint] = useState(row.endpoint_ref);
  const [credential, setCredential] = useState(row.credential_ref ?? "");
  const [version, setVersion] = useState(row.model_version);
  const [capabilities, setCapabilities] = useState(() => JSON.stringify(row.capabilities.map((capability) => ({ ...capability, visibility: "draft" })), null, 2));
  const [reason, setReason] = useState("");
  const [check, setCheck] = useState<ServiceCheck | null>(null);
  const [release, setRelease] = useState<ConfigRelease | null>(null);
  const platform = me.roles.includes("platform_admin");
  const canWrite = me.permissions.includes("services:write") && (platform || (me.service_ids.includes(id) && row.owner_user_id === me.user_id));
  const canPublish = me.permissions.includes("services:publish");
  const revision = { expected_revision: row.revision, reason };
  const save = useAdminMutation(async () => {
    const parsed: unknown = JSON.parse(capabilities);
    if (!Array.isArray(parsed) || !parsed.length || parsed.some((item) => !item || typeof item !== "object" || typeof item.id !== "string" || typeof item.version !== "string" || !item.input_schema)) throw new SyntaxError("Invalid capabilities");
    const draft: ServiceDraft = { ...revision, name, owner_user_id: owner, transport, endpoint_ref: endpoint, credential_ref: credential || null, model_version: version, capabilities: parsed as CapabilityVersion[] };
    return api.saveServiceDraft(id, draft);
  }, (value) => { updated(value); setCheck(null); setRelease(null); });
  const inspect = useAdminMutation((kind: "discovery" | "schema" | "connectivity") => kind === "discovery" ? api.discoverService(id, revision) : api.checkService(id, { ...revision, kind }), (value, kind) => { setCheck(value); if (kind === "schema" && value.status === "passed") updated({ ...row, state: "validated", revision: value.revision, schema_digest: value.schema_digest }); });
  const createRelease = useAdminMutation(() => api.createConfigRelease({ expected_revision: 0, reason, services: [{ service_id: id, revision: row.revision }] }), setRelease);
  const publish = useAdminMutation(() => {
    if (!release) throw new Error("No release");
    return api.publishConfigRelease(release.release_id, { expected_revision: release.revision, reason });
  }, setRelease);
  const busy = save.isPending || inspect.isPending || createRelease.isPending || publish.isPending;
  const dirty = name !== row.name || owner !== row.owner_user_id || transport !== row.transport || endpoint !== row.endpoint_ref || credential !== (row.credential_ref ?? "") || version !== row.model_version || capabilities !== JSON.stringify(row.capabilities.map((capability) => ({ ...capability, visibility: "draft" })), null, 2);
  const eligible = reason.trim().length >= 5 && !!id && !!row.revision && !dirty;
  return <section className="admin-editor"><h2>{row.name || t("admin.newService")}</h2><p className="admin-meta">{t("admin.revision", { revision: row.revision })}{row.published_revision !== null && ` · ${t("admin.publishedRevision", { revision: row.published_revision })}`}</p><form className="admin-form" onSubmit={(event) => { event.preventDefault(); if (canWrite) save.mutate(undefined); }}>
    <fieldset disabled={busy || (row.service_id ? !canWrite : !me.permissions.includes("services:write"))}><label>{t("admin.serviceId")}{!row.service_id && !platform ? <select value={id} onChange={(event) => setId(event.target.value)}><option value="">{t("admin.selectService")}</option>{me.service_ids.map((serviceId) => <option key={serviceId}>{serviceId}</option>)}</select> : <input required pattern="[a-zA-Z0-9_.-]{1,120}" value={id} readOnly={!!row.service_id} onChange={(event) => setId(event.target.value)} />}</label><label>{t("admin.serviceName")}<input required value={name} onChange={(event) => setName(event.target.value)} /></label><label>{t("admin.owner")}<input required value={owner} readOnly={!platform} onChange={(event) => setOwner(event.target.value)} /></label><label>{t("admin.transport")}<select value={transport} onChange={(event) => setTransport(event.target.value as ServiceDraft["transport"])}><option value="worker_pull">Worker</option><option value="http">HTTP</option><option value="mcp">MCP</option></select></label><label>{t("admin.endpointRef")}<input required value={endpoint} onChange={(event) => setEndpoint(event.target.value)} /></label><label>{t("admin.credentialRef")}<input value={credential} onChange={(event) => setCredential(event.target.value)} /></label><label>{t("admin.modelVersion")}<input required value={version} onChange={(event) => setVersion(event.target.value)} /></label><label>{t("admin.capabilitiesJson")}<textarea className="admin-json" required value={capabilities} onChange={(event) => setCapabilities(event.target.value)} /></label><p className="admin-hint">{t("admin.immutableHint")}</p></fieldset>
    <ReasonField value={reason} onChange={setReason} disabled={busy || (!canWrite && !canPublish)} />
    <MutationFeedback error={save.error ?? inspect.error ?? createRelease.error ?? publish.error} success={save.isSuccess} />
    <div className="admin-actions">{canWrite && <><button type="submit" disabled={busy}>{t("admin.saveDraft")}</button><button type="button" disabled={busy || !eligible} onClick={() => inspect.mutate("discovery")}>{t("admin.discover")}</button><button type="button" disabled={busy || !eligible} onClick={() => inspect.mutate("connectivity")}>{t("admin.check")}</button><button type="button" disabled={busy || !eligible} onClick={() => inspect.mutate("schema")}>{t("admin.validateSchema")}</button></>}{canPublish && <button type="button" disabled={busy || !eligible || row.state !== "validated"} onClick={() => createRelease.mutate(undefined)}>{t("admin.createRelease")}</button>}</div>
    {check && <div className="admin-check-result"><p role={check.status === "failed" ? "alert" : "status"}>{t(check.status === "passed" ? "admin.checkPassed" : "admin.checkFailed")}</p><p>{check.message}</p><p className="admin-meta">{check.checked_at}{check.schema_digest ? ` · ${check.schema_digest}` : ""}</p>{check.discovered_capabilities.length > 0 && <details><summary>{t("admin.discovered")}</summary><pre>{JSON.stringify(check.discovered_capabilities, null, 2)}</pre></details>}</div>}
    {release && <section className="admin-release"><h3>{t("admin.releaseImpact")}</h3><p>{release.release_id} · {t("admin.revision", { revision: release.revision })}</p><ul>{release.impact.map((impact) => <li key={impact}>{impact}</li>)}</ul>{release.state === "published" ? <p role="status">{t("admin.releasePublished")}</p> : canPublish && <button type="button" disabled={busy || reason.trim().length < 5} onClick={() => publish.mutate(undefined)}>{t("admin.publishRelease")}</button>}</section>}
  </form></section>;
}

export function AdminServicesPage() {
  const { api, me } = useAdmin();
  const { t } = useLanguage();
  const query = useAdminList("services", "services:read", api.listServices);
  const rows = query.data?.pages.flatMap((page) => page.items) ?? [];
  const [selected, setSelected] = useState<AdminService | null>(null);
  const canEdit = (row: AdminService) => me.permissions.includes("services:write") && (me.roles.includes("platform_admin") || (me.service_ids.includes(row.service_id) && row.owner_user_id === me.user_id));
  return <><div className="admin-page-actions">{me.permissions.includes("services:write") && (me.roles.includes("platform_admin") || me.service_ids.length > 0) && <button type="button" onClick={() => setSelected({ service_id: "", revision: 0, state: "draft", name: "", owner_user_id: me.user_id, transport: "worker_pull", endpoint_ref: "", credential_ref: null, model_version: "", capabilities: [], published_revision: null, schema_digest: null })}>{t("admin.newService")}</button>}</div><div className="admin-split"><AdminListFrame loading={query.isPending} error={query.error} empty={!rows.length} hasMore={query.hasNextPage} loadingMore={query.isFetchingNextPage} refresh={() => query.refetch()} more={() => query.fetchNextPage()}><div className="admin-table-scroll" tabIndex={0} aria-label={t("admin.scrollList")}><table><thead><tr><th>{t("admin.serviceName")}</th><th>{t("admin.status")}</th><th>{t("admin.owner")}</th><th>{t("admin.actions")}</th></tr></thead><tbody>{rows.map((row) => <tr key={row.service_id}><th scope="row">{row.name}<small>{row.service_id}</small></th><td><StateLabel state={row.state} /></td><td>{row.owner_user_id}</td><td><button type="button" aria-label={`${t(canEdit(row) ? "admin.edit" : "admin.view")} ${row.name}`} onClick={() => setSelected(row)}>{t(canEdit(row) ? "admin.edit" : "admin.view")}</button></td></tr>)}</tbody></table></div></AdminListFrame>{selected && <ServiceEditor key={selected.service_id || "new"} row={selected} updated={setSelected} />}</div></>;
}
