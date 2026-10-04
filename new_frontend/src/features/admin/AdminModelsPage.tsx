import { useState } from "react";
import type { AdminModel, ModelDraft } from "../../api/admin";
import { useLanguage } from "../../i18n/LanguageProvider";
import { AdminListFrame, MutationFeedback, ReasonField, StateLabel, splitIds } from "./AdminControls";
import { useAdmin, useAdminList, useAdminMutation } from "./adminQueries";

function ModelEditor({ row, updated }: { row: AdminModel; updated: (row: AdminModel) => void }) {
  const { api, me } = useAdmin();
  const { t } = useLanguage();
  const policy = row.draft ?? row.published;
  const [users, setUsers] = useState(() => policy?.allowed_user_ids.join(", ") ?? "");
  const [groups, setGroups] = useState(() => policy?.allowed_group_ids.join(", ") ?? "");
  const [purposes, setPurposes] = useState<ModelDraft["purposes"]>(() => policy?.purposes ?? ["chat"]);
  const [defaults, setDefaults] = useState<ModelDraft["purposes"]>(() => policy?.default_for_purposes ?? []);
  const [images, setImages] = useState(policy?.supports_images ?? false);
  const [levels, setLevels] = useState<ModelDraft["reasoning_levels"]>(() => policy?.reasoning_levels ?? []);
  const [reason, setReason] = useState("");
  const canWrite = me.permissions.includes("models:write");
  const canPublish = me.permissions.includes("models:publish");
  const draft: ModelDraft = { expected_revision: row.revision, reason, allowed_user_ids: splitIds(users), allowed_group_ids: splitIds(groups), purposes, supports_images: images, reasoning_levels: levels, default_for_purposes: defaults };
  const save = useAdminMutation(() => api.saveModelDraft(row.id, draft), updated);
  const publish = useAdminMutation(() => api.publishModel(row.id, { expected_revision: row.revision, reason }), updated);
  const retire = useAdminMutation(() => api.retireModel(row.id, { expected_revision: row.revision, reason }), updated);
  const persisted = row.draft && { ...row.draft, expected_revision: row.revision, reason };
  const dirty = JSON.stringify(draft) !== JSON.stringify(persisted);
  const busy = save.isPending || publish.isPending || retire.isPending;
  return <section className="admin-editor"><h2>{row.id}</h2><p className="admin-meta">{t("admin.revision", { revision: row.revision })}</p><form className="admin-form" onSubmit={(event) => { event.preventDefault(); if (canWrite) save.mutate(undefined); }}>
    <fieldset disabled={!canWrite || busy}><label>{t("admin.allowedUsers")}<textarea value={users} onChange={(event) => setUsers(event.target.value)} /></label><label>{t("admin.allowedGroups")}<textarea value={groups} onChange={(event) => setGroups(event.target.value)} /></label><p className="admin-hint">{t("admin.emptyAllowlist")}</p>
      <div className="admin-checkboxes">{(["chat", "analysis"] as const).map((purpose) => <label key={purpose}><input type="checkbox" checked={purposes.includes(purpose)} onChange={(event) => { setPurposes(event.target.checked ? [...purposes, purpose] : purposes.filter((item) => item !== purpose)); if (!event.target.checked) setDefaults(defaults.filter((item) => item !== purpose)); }} />{t(purpose === "chat" ? "admin.chatPurpose" : "admin.analysisPurpose")}</label>)}</div>
      <label className="admin-check"><input type="checkbox" checked={images} disabled={!row.gateway.supports_images} onChange={(event) => setImages(event.target.checked)} />{t("admin.supportsImages")}</label>
      {!!row.gateway.reasoning_levels?.length && <div className="admin-checkboxes">{row.gateway.reasoning_levels.map((level) => <label key={level}><input type="checkbox" checked={levels.includes(level)} onChange={(event) => setLevels(event.target.checked ? [...levels, level] : levels.filter((item) => item !== level))} />{level}</label>)}</div>}
      <div className="admin-checkboxes">{purposes.map((purpose) => <label key={purpose}><input type="checkbox" checked={defaults.includes(purpose)} onChange={(event) => setDefaults(event.target.checked ? [...defaults, purpose] : defaults.filter((item) => item !== purpose))} />{t(purpose === "chat" ? "admin.defaultChat" : "admin.defaultAnalysis")}</label>)}</div>
    </fieldset>
    <ReasonField value={reason} onChange={setReason} disabled={busy || (!canWrite && !canPublish)} />
    <MutationFeedback error={save.error ?? publish.error ?? retire.error} success={save.isSuccess} />
    {retire.isSuccess && <p role="status">{t("admin.modelRetired")}</p>}
    {canPublish && row.state === "published" && <button type="button" disabled={busy || reason.trim().length < 5} onClick={() => retire.mutate(undefined)}>{t("admin.retireModel")}</button>}
    <div className="admin-actions">{canWrite && <button type="submit" disabled={busy || !purposes.length}>{t("admin.saveDraft")}</button>}{canPublish && <button type="button" disabled={busy || dirty || reason.trim().length < 5 || !row.gateway_available} onClick={() => publish.mutate(undefined)}>{t("admin.publishModel")}</button>}</div>{canPublish && dirty && <p className="admin-hint">{t("admin.saveFirst")}</p>}
  </form></section>;
}

export function AdminModelsPage() {
  const { api, me } = useAdmin();
  const { t } = useLanguage();
  const query = useAdminList("models", "models:read", api.listAdminModels);
  const rows = query.data?.pages.flatMap((page) => page.items) ?? [];
  const [selected, setSelected] = useState<AdminModel | null>(null);
  return <div className="admin-split"><AdminListFrame loading={query.isPending} error={query.error} empty={!rows.length} hasMore={query.hasNextPage} loadingMore={query.isFetchingNextPage} refresh={() => query.refetch()} more={() => query.fetchNextPage()}><div className="admin-table-scroll" tabIndex={0} aria-label={t("admin.scrollList")}><table><thead><tr><th>{t("admin.alias")}</th><th>{t("admin.status")}</th><th>{t("admin.gateway")}</th><th>{t("admin.actions")}</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><th scope="row">{row.id}</th><td><StateLabel state={row.state} /></td><td>{t(row.gateway_available ? "admin.available" : "admin.unavailableShort")}</td><td><button type="button" aria-label={`${t(me.permissions.includes("models:write") ? "admin.edit" : "admin.view")} ${row.id}`} onClick={() => setSelected(row)}>{t(me.permissions.includes("models:write") ? "admin.edit" : "admin.view")}</button></td></tr>)}</tbody></table></div></AdminListFrame>{selected && <ModelEditor key={selected.id} row={selected} updated={setSelected} />}</div>;
}
