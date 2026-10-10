import { useState } from "react";
import type { AdminUser, ResourceCounter } from "../../api/admin";
import { useLanguage } from "../../i18n/LanguageProvider";
import { AdminListFrame, AdminTable, MutationFeedback, ReasonField } from "./AdminControls";
import { useAdmin, useAdminList, useAdminMutation } from "./adminQueries";

function Counter({ name, counter, scale = 1 }: { name: string; counter: ResourceCounter; scale?: number }) {
  const { t } = useLanguage();
  return <section role="group" aria-label={name} className="admin-counter"><h3>{name}</h3><dl>{(["limit", "used", "reserved", "remaining"] as const).map((field) => <div key={field}><dt>{t(`admin.${field}`)}</dt><dd>{counter[field] / scale}</dd></div>)}</dl></section>;
}

function UserEditor({ row, updated }: { row: AdminUser; updated: (row: AdminUser) => void }) {
  const { api, me } = useAdmin();
  const { t } = useLanguage();
  const [tokens, setTokens] = useState(String(row.token_monthly_limit));
  const [gpu, setGpu] = useState(String(row.gpu_daily_minutes));
  const [cpu, setCpu] = useState(String(row.cpu_daily_core_ms / 1000));
  const [concurrency, setConcurrency] = useState(String(row.concurrency_limit));
  const [storage, setStorage] = useState(row.storage_limit_bytes === null ? "" : String(row.storage_limit_bytes));
  const [reason, setReason] = useState("");
  const canWrite = me.permissions.includes("quotas:write");
  const valid = [tokens, gpu, cpu, concurrency, storage].every((value) => value !== "" && Number.isFinite(Number(value)) && Number(value) >= 0) && [Number(tokens), Number(gpu), Math.round(Number(cpu) * 1000), Number(concurrency), Number(storage)].every(Number.isSafeInteger) && Number(concurrency) >= 1;
  const save = useAdminMutation(() => api.saveUserLimits(row.user_id, { expected_revision: row.revision, reason, token_monthly_limit: Number(tokens), gpu_daily_minutes: Number(gpu), cpu_daily_core_ms: Math.round(Number(cpu) * 1000), concurrency_limit: Number(concurrency), storage_limit_bytes: Number(storage) }), updated);
  return <section className="admin-editor"><h2>{row.user_id}</h2><p className="admin-meta">{row.tier} · {t("admin.revision", { revision: row.revision })}</p><div className="admin-counters"><Counter name={t("admin.tokens")} counter={row.tokens} /><Counter name={t("admin.gpuMinutes")} counter={row.gpu} /><Counter name={t("admin.cpuSeconds")} counter={row.cpu} scale={1000} /></div><form className="admin-form" onSubmit={(event) => { event.preventDefault(); if (canWrite && valid) save.mutate(undefined); }}><fieldset disabled={!canWrite || save.isPending}><label>{t("admin.tokenLimit")}<input type="number" min={0} step={1} required value={tokens} onChange={(event) => setTokens(event.target.value)} /></label><label>{t("admin.gpuLimit")}<input type="number" min={0} max={1440} step={1} required value={gpu} onChange={(event) => setGpu(event.target.value)} /></label><label>{t("admin.cpuLimit")}<input type="number" min={0} step={0.001} required value={cpu} onChange={(event) => setCpu(event.target.value)} /></label><label>{t("admin.concurrencyLimit")}<input type="number" min={1} max={1000} step={1} required value={concurrency} onChange={(event) => setConcurrency(event.target.value)} /></label><label>{t("admin.storageLimit")}<input type="number" min={0} step={1} required value={storage} placeholder={row.storage_limit_bytes === null ? t("admin.unlimited") : undefined} onChange={(event) => setStorage(event.target.value)} /></label>{row.storage_limit_bytes === null && <p className="admin-hint">{t("admin.unlimited")}</p>}</fieldset>{canWrite && <><ReasonField value={reason} onChange={setReason} disabled={save.isPending} /><MutationFeedback error={save.error} success={save.isSuccess} /><button type="submit" disabled={save.isPending || !valid || reason.trim().length < 5}>{t("admin.saveLimits")}</button></>}</form></section>;
}

export function AdminUsersPage() {
  const { api, me } = useAdmin();
  const { t } = useLanguage();
  const query = useAdminList("users", "quotas:read", api.listAdminUsers);
  const rows = query.data?.pages.flatMap((page) => page.items) ?? [];
  const [selected, setSelected] = useState<AdminUser | null>(null);
  const editable = me.permissions.includes("quotas:write");
  return <div className="admin-split"><AdminListFrame loading={query.isPending} error={query.error} empty={!rows.length} hasMore={query.hasNextPage} loadingMore={query.isFetchingNextPage} refresh={() => query.refetch()} more={() => query.fetchNextPage()}><p className="admin-hint">{t("admin.counterOrder")}</p><AdminTable rows={rows} rowKey={(row) => row.user_id} columns={[{ label: t("admin.user"), render: (row) => <>{row.user_id}<small>{row.tier}</small></> }, { label: t("admin.gpuMinutes"), render: (row) => `${row.gpu.used} / ${row.gpu.reserved} / ${row.gpu.remaining}` }, { label: t("admin.cpuSeconds"), render: (row) => `${row.cpu.used / 1000} / ${row.cpu.reserved / 1000} / ${row.cpu.remaining / 1000}` }, { label: t("admin.actions"), render: (row) => <button type="button" aria-label={`${t(editable ? "admin.edit" : "admin.view")} ${row.user_id}`} onClick={() => setSelected(row)}>{t(editable ? "admin.edit" : "admin.view")}</button> }]} /></AdminListFrame>{selected && <UserEditor key={selected.user_id} row={selected} updated={setSelected} />}</div>;
}
