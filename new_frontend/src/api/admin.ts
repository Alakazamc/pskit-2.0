/** API contracts generated from the backend OpenAPI document. */
import type * as Generated from "./generated/types.gen";

export type AdminMe = Generated.AdminMe;
export type AdminPage<T> = Omit<Required<Generated.AdminPageAdminModel>, "items"> & { items: T[] };
export type RevisionRequest = Generated.RevisionRequest;
export type ModelDraft = Required<Generated.ModelDraft>;
export type AdminModel = Omit<Required<Generated.AdminModel>, "draft" | "published"> & { draft: ModelDraft | null; published: ModelDraft | null };
export type ServiceDraft = Required<Generated.ServiceDraft>;
export type AdminService = Required<Generated.AdminService>;
export type ServiceCheck = Required<Generated.ServiceCheck>;
export type ConfigRelease = Generated.ConfigRelease;
export type ResourceCounter = Generated.ResourceCounter;
export type LimitsUpdate = Required<Generated.LimitsUpdate>;
export type AdminUser = Generated.AdminUser;
export type AdminJob = Required<Generated.AdminJob>;
export type AdminOperation = Generated.AdminOperation;
export type SandboxSummary = Required<Generated.SandboxSummary>;
export type ReconcileRequest = Generated.ReconcileRequest;
export type LegacyAf3ReconcileRequest = Required<Generated.LegacyAf3ReconcileRequest>;
export type AuditEvent = Generated.AuditEvent;

export interface AdminApi {
  getAdminMe(): Promise<AdminMe>;
  listAdminModels(cursor?: string): Promise<AdminPage<AdminModel>>;
  saveModelDraft(id: string, draft: ModelDraft): Promise<AdminModel>;
  publishModel(id: string, revision: RevisionRequest): Promise<AdminModel>;
  retireModel(id: string, revision: RevisionRequest): Promise<AdminModel>;
  listServices(cursor?: string): Promise<AdminPage<AdminService>>;
  saveServiceDraft(id: string, draft: ServiceDraft): Promise<AdminService>;
  discoverService(id: string, revision: RevisionRequest): Promise<ServiceCheck>;
  checkService(id: string, request: RevisionRequest & { kind: "connectivity" | "schema" }): Promise<ServiceCheck>;
  createConfigRelease(request: RevisionRequest & { services: ConfigRelease["services"] }): Promise<ConfigRelease>;
  publishConfigRelease(id: string, revision: RevisionRequest): Promise<ConfigRelease>;
  listAdminUsers(cursor?: string): Promise<AdminPage<AdminUser>>;
  saveUserLimits(id: string, limits: LimitsUpdate): Promise<AdminUser>;
  listAdminJobs(cursor?: string): Promise<AdminPage<AdminJob>>;
  cancelAdminJob(id: string, revision: RevisionRequest): Promise<AdminOperation>;
  listAdminSandboxes(cursor?: string): Promise<AdminPage<SandboxSummary>>;
  drainAdminSandbox(id: string, revision: RevisionRequest): Promise<AdminOperation>;
  listReconciliation(cursor?: string): Promise<AdminPage<AdminJob>>;
  reconcileAdminJob(id: string, request: ReconcileRequest): Promise<AdminJob>;
  reconcileAdminAf3Job(id: string, request: LegacyAf3ReconcileRequest): Promise<AdminJob>;
  listAuditEvents(cursor?: string): Promise<AdminPage<AuditEvent>>;
}

export type AdminRequest = <T>(path: string, init?: RequestInit) => Promise<T>;
export function createAdminApi(request: AdminRequest): AdminApi {
  const list = <T>(path: string, cursor?: string) => request<AdminPage<T>>(`/admin/${path}?limit=50${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`);
  const change = <T>(path: string, body: unknown, method = "POST") => request<T>(`/admin/${path}`, { method, body: JSON.stringify(body) });
  return {
    getAdminMe: () => request("/admin/me"),
    listAdminModels: (cursor) => list("llm-aliases", cursor),
    saveModelDraft: (id, draft) => change(`llm-aliases/${encodeURIComponent(id)}/draft`, draft, "PUT"),
    publishModel: (id, revision) => change(`llm-aliases/${encodeURIComponent(id)}/publish`, revision),
    retireModel: (id, revision) => change(`llm-aliases/${encodeURIComponent(id)}/retire`, revision),
    listServices: (cursor) => list("services", cursor),
    saveServiceDraft: (id, draft) => change(`services/${encodeURIComponent(id)}/draft`, draft, "PUT"),
    discoverService: (id, revision) => change(`services/${encodeURIComponent(id)}/discovery`, revision),
    checkService: (id, payload) => change(`services/${encodeURIComponent(id)}/checks`, payload),
    createConfigRelease: (payload) => change("config-releases", payload),
    publishConfigRelease: (id, revision) => change(`config-releases/${encodeURIComponent(id)}/publish`, revision),
    listAdminUsers: (cursor) => list("users", cursor),
    saveUserLimits: (id, limits) => change(`users/${encodeURIComponent(id)}/limits`, limits, "PUT"),
    listAdminJobs: (cursor) => list("jobs", cursor),
    cancelAdminJob: (id, revision) => change(`jobs/${encodeURIComponent(id)}/cancel`, revision),
    listAdminSandboxes: (cursor) => list("sandboxes", cursor),
    drainAdminSandbox: (id, revision) => change(`sandboxes/${encodeURIComponent(id)}/drain`, revision),
    listReconciliation: (cursor) => list("usage/reconciliation", cursor),
    reconcileAdminJob: (id, payload) => change(`compute/jobs/${encodeURIComponent(id)}/reconcile`, payload),
    reconcileAdminAf3Job: (id, payload) => change(`af3/jobs/${encodeURIComponent(id)}/reconcile`, payload),
    listAuditEvents: (cursor) => list("audit-events", cursor),
  };
}
