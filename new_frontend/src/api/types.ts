import type * as Api from "./generated/types.gen";
import type * as Events from "./generated-events";
import type { AdminApi } from "./admin";

export type UserIdentity = Api.UserIdentity;

export type DemoLoginResponse = Api.DemoLoginResponse;

export type AuthSessionResponse = Api.AuthSessionResponse;
export type SignupResponse = Api.SignupResponse & { session: AuthSessionResponse | null };
export type RecoveryResponse = Required<Api.RecoveryResponse>;

export type QuotaCounter = Api.QuotaCounter;

export type GpuQuota = Api.GpuQuota;

export type UsageSnapshot = Api.UsageSnapshot;
export type UsageEntry = Api.UsageEntry;

export type Project = Api.Project;
export type ProjectIcon = NonNullable<Project["icon"]>;
export type ProjectSkillSettings = Required<Api.ProjectSkillSettings>;
export type Session = Api.Session;
export type RunStatus = Api.RunStatus;
export type ContextRef = Api.ContextRef;
export type CatalogItem = Api.CatalogItem;
export type FileRef = Api.FileRef;
export type ArtifactRef = Api.ArtifactRef;
export type ArtifactPreview = Api.ArtifactPreview;
export type MessageRequest = Api.MessageRequest & {
  attachments: ContextRef[];
  skills: ContextRef[];
  resources: ContextRef[];
};
export type ModelOption = Api.ModelOption;
type Tagged<T extends { type?: string }> = T & { type: NonNullable<T["type"]> };
export type MessagePart =
  | Tagged<Api.TextPart>
  | Tagged<Api.ToolCallPart>
  | Tagged<Api.ToolResultPart>
  | Tagged<Api.FilePart>
  | Tagged<Api.ArtifactPart>
  | Tagged<Api.CitationPart>
  | Tagged<Api.ProgressPart>
  | Tagged<Api.ErrorPart>;
export type Message = Omit<Api.Message, "parts"> & { parts: MessagePart[] };
export type PlanStep = Events.PlanStep;
export type RunEvent = Events.RunEvent;
export type McpTool = Required<Api.McpTool>;
export type McpResult = Api.McpInvokeResult & { status: "completed" };
export type ToolRun = Api.ToolRun & { project_id: string | null };
export type Af3Job = Api.Af3Job;
export type Af3JobRequest = Api.Af3JobRequest;

export interface ResearchApi extends AdminApi {
  startAnonymous(captchaToken?: string): Promise<AuthSessionResponse>;
  beginGuestEmailUpgrade(email: string): Promise<Api.EmailUpgradeStartResponse>;
  verifyGuestEmailUpgrade(email: string, code: string): Promise<AuthSessionResponse>;
  beginGuestGoogleUpgrade(): Promise<Api.GoogleUpgradeStartResponse>;
  loginDemo(email: string): Promise<DemoLoginResponse>;
  loginEmail(email: string, password: string): Promise<AuthSessionResponse>;
  signupEmail(email: string, password: string): Promise<SignupResponse>;
  requestPasswordRecovery(email: string): Promise<RecoveryResponse>;
  verifyEmailCode(email: string, code: string, kind: "signup" | "recovery"): Promise<AuthSessionResponse>;
  updatePassword(accessToken: string, password: string): Promise<void>;
  refreshAuth(): Promise<AuthSessionResponse>;
  logoutAuth(): Promise<void>;
  googleLoginUrl(): string;
  getMe(): Promise<UserIdentity>;
  getUsage(): Promise<UsageSnapshot>;
  getUsageEntries(): Promise<UsageEntry[]>;
  getProjects(): Promise<Project[]>;
  createProject(name: string, description?: string, icon?: ProjectIcon): Promise<Project>;
  renameProject(id: string, name: string): Promise<Project>;
  setProjectIcon(id: string, icon: ProjectIcon): Promise<Project>;
  archiveProject(id: string): Promise<void>;
  getProjectSkills(id: string): Promise<ProjectSkillSettings>;
  setProjectSkills(id: string, settings: ProjectSkillSettings): Promise<ProjectSkillSettings>;
  getSessions(projectId: string | null): Promise<Session[]>;
  getSession(id: string, projectId?: string | null): Promise<Session>;
  createSession(projectId: string | null, title: string): Promise<Session>;
  renameSession(id: string, title: string, projectId?: string | null): Promise<Session>;
  archiveSession(id: string, projectId?: string | null): Promise<void>;
  moveSession(id: string, targetProjectId: string, sourceProjectId?: string | null): Promise<Session>;
  getMessages(sessionId: string, projectId?: string | null): Promise<Message[]>;
  getSkills(): Promise<CatalogItem[]>;
  getResources(): Promise<CatalogItem[]>;
  getModels(): Promise<ModelOption[]>;
  getFiles(): Promise<FileRef[]>;
  uploadFile(file: File): Promise<FileRef>;
  downloadFile(id: string): Promise<Blob>;
  deleteFile(id: string): Promise<void>;
  getArtifacts(): Promise<ArtifactRef[]>;
  getArtifactPreview(id: string): Promise<ArtifactPreview>;
  downloadArtifact(id: string): Promise<Blob>;
  sendMessage(sessionId: string, message: MessageRequest, idempotencyKey?: string, projectId?: string | null): Promise<{ run_id: string }>;
  getRunEvents(runId: string, after?: string): Promise<RunEvent[]>;
  cancelRun(runId: string): Promise<RunStatus>;
  decideApproval(runId: string, approvalId: string, decision: Api.ApprovalDecisionRequest["decision"]): Promise<Api.ApprovalDecisionResponse>;
  streamRunEvents(runId: string, after: string | undefined, onEvent: (event: RunEvent) => void, signal: AbortSignal): Promise<void>;
  getMcpTools(): Promise<McpTool[]>;
  invokeMcpTool(name: string, args: Record<string, unknown>, idempotencyKey?: string): Promise<McpResult>;
  getToolRuns(tool?: string): Promise<ToolRun[]>;
  saveToolRunToProject(id: string, projectId: string): Promise<ToolRun>;
  submitAf3(payload: Af3JobRequest, idempotencyKey: string): Promise<Af3Job>;
  getAf3Job(id: string): Promise<Af3Job>;
  cancelAf3Job(id: string): Promise<Af3Job>;
}
