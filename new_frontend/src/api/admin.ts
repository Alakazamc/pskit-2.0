/** API contracts generated from the backend OpenAPI document. */
import type * as Generated from "./generated/types.gen";
import type { CatalogItem, SkillDetail } from "./types";

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
export type McpProbeRequest = Generated.McpProbeRequest;
export type ProbeSnapshot = Generated.ProbeSnapshot;
export type DiscoveryRequest = Generated.DiscoveryRequest;
export type DiscoverySnapshot = Generated.DiscoverySnapshot;
export type ToolProductDraft = Generated.ToolProductDraftOutput;
export type ProductDraftRequest = Generated.ProductDraftRequest;
export type AcceptanceSuite = Generated.AcceptanceSuite;
export type QualificationRequest = Generated.QualificationRequest;
export type QualificationReport = Generated.QualificationReport;
export type PublishProductRequest = Generated.PublishProductRequest;
export type PublishedToolProduct = Generated.PublishedToolProduct;
export type ReleaseActionRequest = Generated.ReleaseActionRequest;

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
  probeMcp(request: McpProbeRequest): Promise<ProbeSnapshot>;
  getMcpProbe(id: string): Promise<ProbeSnapshot>;
  discoverMcp(serviceId: string, request: DiscoveryRequest): Promise<DiscoverySnapshot>;
  saveToolProductDraft(productId: string, request: ProductDraftRequest): Promise<ToolProductDraft>;
  qualifyToolProduct(productId: string, request: QualificationRequest): Promise<QualificationReport>;
  getToolProductQualification(id: string): Promise<QualificationReport>;
  publishToolProduct(reportId: string, request: PublishProductRequest): Promise<PublishedToolProduct>;
  suspendToolProduct(releaseId: string, request: ReleaseActionRequest): Promise<PublishedToolProduct>;
  rollbackToolProduct(releaseId: string, request: ReleaseActionRequest): Promise<PublishedToolProduct>;
  listSkillReviews(): Promise<SkillDetail[]>;
  reviewSkill(id: string, version: number, decision: "approve" | "reject", reason: string): Promise<CatalogItem>;
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
    probeMcp: (payload) => change("mcp-probes", payload),
    getMcpProbe: (id) => request(`/admin/mcp-probes/${encodeURIComponent(id)}`),
    discoverMcp: (serviceId, payload) => change(`services/${encodeURIComponent(serviceId)}/discoveries`, payload),
    saveToolProductDraft: (productId, payload) => change(`tool-products/${encodeURIComponent(productId)}/draft`, payload, "PUT"),
    qualifyToolProduct: (productId, payload) => change(`tool-products/${encodeURIComponent(productId)}/qualifications`, payload),
    getToolProductQualification: (id) => request(`/admin/qualifications/${encodeURIComponent(id)}`),
    publishToolProduct: (reportId, payload) => change(`tool-product-releases/${encodeURIComponent(reportId)}/publish`, payload),
    suspendToolProduct: (releaseId, payload) => change(`tool-product-releases/${encodeURIComponent(releaseId)}/suspend`, payload),
    rollbackToolProduct: (releaseId, payload) => change(`tool-product-releases/${encodeURIComponent(releaseId)}/rollback`, payload),
    listSkillReviews: () => request("/admin/skills/reviews"),
    reviewSkill: (id, version, decision, reason) => change(`skills/${encodeURIComponent(id)}/versions/${version}/review`, { decision, reason }),
  };
}
