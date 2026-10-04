import { useState, type ReactNode } from "react";
import type { AdminOperation, RevisionRequest } from "../../api/admin";
import { useLanguage } from "../../i18n/LanguageProvider";
import type { TranslationKey } from "../../i18n/translations";
import { MutationFeedback, ReasonField, StateLabel } from "./AdminControls";
import { useAdminMutation } from "./adminQueries";

export function AdminOperationEditor({ id, revision, canAct, confirmed = false, label, hint, action, children }: { id: string; revision: number; canAct: boolean; confirmed?: boolean; label: TranslationKey; hint: TranslationKey; action: (request: RevisionRequest) => Promise<AdminOperation>; children: ReactNode }) {
  const { t } = useLanguage();
  const [reason, setReason] = useState("");
  const operation = useAdminMutation(() => action({ expected_revision: revision, reason }));
  return <section className="admin-editor"><h2>{id}</h2><p className="admin-meta">{t("admin.revision", { revision })}</p>{children}{Boolean(canAct || operation.data || operation.error) && <form className="admin-form" onSubmit={(event) => { event.preventDefault(); if (canAct) operation.mutate(undefined); }}><p className="admin-hint">{t(hint)}</p><ReasonField value={reason} onChange={setReason} disabled={!canAct || operation.isPending} /><MutationFeedback error={operation.error} />{operation.data && <p role="status"><StateLabel state={confirmed ? "confirmed" : operation.data.state} /></p>}{canAct && <button type="submit" disabled={operation.isPending || !!operation.data || reason.trim().length < 5}>{t(label)}</button>}</form>}</section>;
}
