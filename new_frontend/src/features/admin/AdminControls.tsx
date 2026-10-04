import type { ReactNode } from "react";
import { useLanguage } from "../../i18n/LanguageProvider";
import type { TranslationKey } from "../../i18n/translations";
import { adminErrorKey } from "./adminQueries";

export function AdminListFrame({ loading, error, empty, hasMore, loadingMore, refresh, more, children }: { loading: boolean; error: unknown; empty: boolean; hasMore: boolean; loadingMore: boolean; refresh: () => unknown; more: () => unknown; children: ReactNode }) {
  const { t } = useLanguage();
  return <section className="admin-list"><div className="admin-list-tools"><button type="button" onClick={refresh}>{t("admin.refresh")}</button></div>{loading ? <p role="status">{t("admin.loading")}</p> : error ? <p role="alert">{t(adminErrorKey(error))}</p> : empty ? <p className="admin-empty">{t("admin.empty")}</p> : children}{hasMore && <button type="button" disabled={loadingMore} className="admin-more" onClick={more}>{t(loadingMore ? "admin.loading" : "admin.more")}</button>}</section>;
}

export function MutationFeedback({ error, success }: { error: unknown; success?: boolean }) {
  const { t } = useLanguage();
  return error ? <p role="alert" className="admin-error">{t(adminErrorKey(error))}</p> : success ? <p role="status">{t("admin.saved")}</p> : null;
}

export function ReasonField({ value, onChange, disabled = false }: { value: string; onChange: (value: string) => void; disabled?: boolean }) {
  const { t } = useLanguage();
  return <label>{t("admin.reason")}<textarea required minLength={5} maxLength={500} value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)} /></label>;
}

export function StateLabel({ state }: { state: string }) {
  const { t } = useLanguage();
  const states: Record<string, TranslationKey> = { draft: "admin.state.draft", validated: "admin.state.validated", published: "admin.state.published", retired: "admin.state.retired", queued: "admin.state.queued", running: "admin.state.running", cancelling: "admin.state.cancelling", cancelled: "admin.state.cancelled", completed: "admin.state.completed", failed: "admin.state.failed", reserved: "admin.state.reserved", settled: "admin.state.settled", released: "admin.state.released", pending_reconciliation: "admin.state.pending", ready: "admin.state.ready", draining: "admin.state.draining", replacing: "admin.state.replacing", error: "admin.state.failed", stopped: "admin.state.stopped", unknown: "admin.state.unknown", stopping: "admin.state.cancelling", requested: "admin.stopRequested", confirmed: "admin.stopConfirmed" };
  return <span className={`admin-state admin-state-${state}`}>{states[state] ? t(states[state]) : state}</span>;
}

export const splitIds = (value: string) => [...new Set(value.split(/[\s,]+/).map((item) => item.trim()).filter(Boolean))];

export function AdminTable<T>({ rows, rowKey, columns }: { rows: T[]; rowKey: (row: T) => string; columns: { label: string; render: (row: T) => ReactNode }[] }) {
  const { t } = useLanguage();
  return <div className="admin-table-scroll" tabIndex={0} aria-label={t("admin.scrollList")}><table><thead><tr>{columns.map((column) => <th key={column.label}>{column.label}</th>)}</tr></thead><tbody>{rows.map((row) => <tr key={rowKey(row)}>{columns.map((column, index) => index === 0 ? <th scope="row" key={column.label}>{column.render(row)}</th> : <td key={column.label}>{column.render(row)}</td>)}</tr>)}</tbody></table></div>;
}
