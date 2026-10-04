import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Panel, Group, Separator } from "react-resizable-panels";
import { CatalogLibrary } from "../../components/catalog/CatalogLibrary";
import { ArrowLeft, ChevronRight, LogOut, Menu, PanelRightClose, PanelRightOpen, Sparkles } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import type { MessageRequest, ResearchApi, UserIdentity } from "../../api/types";
import { AgentPanel } from "../chat/AgentPanel";
import { Composer } from "../chat/Composer";
import { conversationDraftScope } from "../chat/composerDrafts";
import { Conversation } from "../chat/Conversation";
import { emptyRun } from "../chat/events";
import { useRunEvents } from "../chat/useRunEvents";
import { NavigationRail } from "./NavigationRail";
import { ProjectSidebar } from "./ProjectSidebar";
import { LanguageSwitch, useLanguage } from "../../i18n/LanguageProvider";
import { errorTranslationKey } from "../../i18n/errors";
import type { TranslationKey } from "../../i18n/translations";
import { projectPath, sessionPath } from "../mono/sessionPaths";

export function WorkspacePage({ api, user, onLogout }: { api: ResearchApi; user: UserIdentity; onLogout: () => void }) {
  const { t } = useLanguage();
  const query = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const projectSegment = location.pathname.match(/^\/p\/([^/]+)/)?.[1];
  const sessionSegment = location.pathname.match(/(?:\/c\/|^\/session\/)([^/]+)/)?.[1];
  const sessionId = sessionSegment ? decodeURIComponent(sessionSegment) : null;
  const draftScope = conversationDraftScope(user.id, sessionId, projectSegment ? decodeURIComponent(projectSegment) : null);
  const section = location.pathname.startsWith("/skills") ? "skills" : location.pathname.startsWith("/resources") ? "resources" : location.pathname.startsWith("/settings") ? "settings" : "projects";
  const [runId, setRunId] = useState<string | null>(null);
  const pendingSend = useRef<{ sessionId: string; body: string; key: string } | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<TranslationKey | null>(null);
  const [showAgent, setShowAgent] = useState(true);
  const [showMobileSidebar, setShowMobileSidebar] = useState(false);
  const [windowWidth, setWindowWidth] = useState(() => window.innerWidth);
  useEffect(() => {
    const update = () => setWindowWidth(window.innerWidth);
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);
  const agentVisible = showAgent && windowWidth > 1100;
  const sidebarVisible = windowWidth > 700;
  const projects = useQuery({ queryKey: ["projects", user.id], queryFn: () => api.getProjects() });
  const projectId = projectSegment ? decodeURIComponent(projectSegment) : projects.data?.[0]?.id;
  const personalProjectId = `project-${user.id}`;
  const scopeProjectId = projectId === personalProjectId ? null : projectId;
  const activeProject = projects.data?.find((item) => item.id === projectId);
  const sessions = useQuery({ queryKey: ["sessions", user.id, projectId], queryFn: () => api.getSessions(scopeProjectId ?? null), enabled: !!projectId });
  const visibleRunId = runId ?? sessions.data?.find((item) => item.id === sessionId)?.latest_run_id ?? null;
  const messages = useQuery({ queryKey: ["messages", user.id, sessionId], queryFn: () => api.getMessages(sessionId!, scopeProjectId), enabled: !!sessionId });
  const usage = useQuery({ queryKey: ["usage", user.id], queryFn: () => api.getUsage() });
  const skills = useQuery({ queryKey: ["skills", user.id], queryFn: () => api.getSkills() });
  const resources = useQuery({ queryKey: ["resources", user.id], queryFn: () => api.getResources() });
  const models = useQuery({ queryKey: ["models", user.id], queryFn: () => api.getModels() });
  const files = useQuery({ queryKey: ["files", user.id], queryFn: () => api.getFiles() });
  const artifacts = useQuery({ queryKey: ["artifacts", user.id], queryFn: () => api.getArtifacts() });
  const onCompleted = useCallback(() => {
    void query.invalidateQueries({ queryKey: ["sessions", user.id, projectId] });
    void query.invalidateQueries({ queryKey: ["messages", user.id, sessionId] });
    void query.invalidateQueries({ queryKey: ["usage", user.id] });
    void query.invalidateQueries({ queryKey: ["artifacts", user.id] });
  }, [query, user.id, projectId, sessionId]);
  const run = useRunEvents(api, visibleRunId, onCompleted);
  const send = async (payload: MessageRequest) => {
    if (!sessionId) return false;
    const body = JSON.stringify(payload);
    if (pendingSend.current?.sessionId !== sessionId || pendingSend.current?.body !== body) {
      pendingSend.current = { sessionId, body, key: crypto.randomUUID() };
    }
    setSending(true); setError(null);
    try {
      const result = await api.sendMessage(sessionId, payload, pendingSend.current.key, scopeProjectId);
      pendingSend.current = null;
      setRunId(result.run_id);
      await query.invalidateQueries({ queryKey: ["messages", user.id, sessionId] });
      await query.invalidateQueries({ queryKey: ["usage", user.id] });
      await query.invalidateQueries({ queryKey: ["sessions", user.id, projectId] });
      return true;
    } catch (caught) { setError(errorTranslationKey(caught) ?? "workspace.sendFailed"); return false; }
    finally { setSending(false); }
  };
  const selectSession = (project: string, session: string) => { setRunId(null); setShowMobileSidebar(false); navigate(sessionPath(project, personalProjectId, session)); };
  const selectProject = (project: string) => { setRunId(null); setShowMobileSidebar(false); navigate(projectPath(project)); };
  const createProject = async (name: string) => {
    const project = await api.createProject(name);
    await query.invalidateQueries({ queryKey: ["projects", user.id] });
    selectProject(project.id);
  };
  const createSession = async (title: string) => {
    if (!projectId) return;
    const session = await api.createSession(scopeProjectId ?? null, title, true);
    await query.invalidateQueries({ queryKey: ["sessions", user.id, projectId] });
    selectSession(projectId, session.id);
  };
  const upload = async (file: File) => {
    const ref = await api.uploadFile(file);
    await query.invalidateQueries({ queryKey: ["files", user.id] });
    return ref;
  };
  const downloadFile = async (id: string, name: string) => {
    const blob = await api.downloadFile(id);
    const url = URL.createObjectURL(blob);
    try {
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = name.replaceAll("/", "_").replaceAll("\\", "_");
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
    } finally { URL.revokeObjectURL(url); }
  };
  const deleteFile = async (id: string) => {
    await api.deleteFile(id);
    await query.invalidateQueries({ queryKey: ["files", user.id] });
  };
  const cancel = async () => {
    if (!visibleRunId) return;
    try {
      await api.cancelRun(visibleRunId);
      setError(null);
      onCompleted();
    } catch { setError("workspace.cancelFailed"); }
  };
  const decideApproval = async (approvalId: string, decision: "approved" | "rejected") => {
    if (!visibleRunId) return;
    try {
      await api.decideApproval(visibleRunId, approvalId, decision);
      setError(null);
      await query.invalidateQueries({ queryKey: ["usage", user.id] });
    } catch (caught) { setError(errorTranslationKey(caught) ?? "workspace.approvalFailed"); }
  };
  return <div className="app-shell">
    <NavigationRail />
    {!sidebarVisible && showMobileSidebar && <div className="mobile-sidebar-backdrop" onClick={() => setShowMobileSidebar(false)}><div className="mobile-sidebar-content" onClick={(event) => event.stopPropagation()}><ProjectSidebar projects={projects.data ?? []} sessions={sessions.data ?? []} files={files.data ?? []} selectedProject={projectId ?? null} selected={sessionId} user={user} usage={usage.data} onSelectProject={selectProject} onSelect={selectSession} onCreateProject={createProject} onCreateSession={createSession} onDownloadFile={downloadFile} onDeleteFile={deleteFile} /></div></div>}
    <div className="desktop-panels"><Group orientation="horizontal" className="workspace-panels">
      {sidebarVisible && <><Panel defaultSize="260px" minSize="220px" maxSize="360px" className="sidebar-panel"><ProjectSidebar projects={projects.data ?? []} sessions={sessions.data ?? []} files={files.data ?? []} selectedProject={projectId ?? null} selected={sessionId} user={user} usage={usage.data} onSelectProject={selectProject} onSelect={selectSession} onCreateProject={createProject} onCreateSession={createSession} onDownloadFile={downloadFile} onDeleteFile={deleteFile} /></Panel><Separator className="resize-handle" /></>}
      <Panel minSize={sidebarVisible ? "360px" : "0px"} className="main-panel">
        <div className="main-header">
          <div className="breadcrumb">{!sidebarVisible && <button className="mobile-menu-button" aria-label={t("workspace.openSessions")} onClick={() => setShowMobileSidebar(true)}><Menu size={19} /></button>}<span>{activeProject?.name ?? t("sidebar.workspace")}</span><ChevronRight size={15} /><b>{section === "projects" ? sessionId ? sessions.data?.find((item) => item.id === sessionId)?.title ?? t("workspace.session") : t("workspace.overview") : t(section === "skills" ? "workspace.skillsLibrary" : section === "resources" ? "workspace.resourcesLibrary" : "nav.settings")}</b></div>
          <div className="header-actions">{windowWidth > 1100 && <button aria-label={t(showAgent ? "workspace.collapsePanel" : "workspace.expandPanel")} onClick={() => setShowAgent(!showAgent)}>{showAgent ? <PanelRightClose size={19} /> : <PanelRightOpen size={19} />}</button>}<button aria-label={t("workspace.logout")} onClick={onLogout}><LogOut size={18} /></button></div>
        </div>
        {section !== "projects" ? <div className="catalog-page"><button className="back-link" onClick={() => navigate("/g")}><ArrowLeft size={16} /> {t("workspace.backProjects")}</button><div className="eyebrow">{t("workspace.eyebrow")}</div><h1>{t(section === "skills" ? "workspace.skillsTitle" : section === "resources" ? "workspace.resourcesTitle" : "workspace.settingsTitle")}</h1><p>{t(section === "skills" ? "workspace.skillsDescription" : section === "resources" ? "workspace.resourcesDescription" : "workspace.settingsDescription")}</p>{section === "settings" && <section className="settings-card"><div><h2>{t("language.label")}</h2><p>{t("language.description")}</p></div><LanguageSwitch /></section>}{section !== "settings" && <CatalogLibrary key={section} kind={section} items={(section === "skills" ? skills.data : resources.data) ?? []} loading={section === "skills" ? skills.isLoading : resources.isLoading} error={section === "skills" ? skills.isError : resources.isError} />}</div> : sessionId ? <>
          {!agentVisible && run.approval && <div className="mobile-approval-banner"><b>{t("agent.approvalRequired", { minutes: run.approval.estimatedMinutes })}</b><button onClick={() => void decideApproval(run.approval!.id, "approved")}>{t("agent.approve")}</button><button onClick={() => void decideApproval(run.approval!.id, "rejected")}>{t("agent.reject")}</button></div>}
          <Conversation messages={messages.data ?? []} run={run} />
          {error && <div className="workspace-error" role="alert">{t(error)}</div>}
          <Composer draftScope={draftScope} onSend={send} onUpload={upload} skills={skills.data ?? []} resources={resources.data ?? []} models={models.data ?? []} disabled={sending} />
        </> : <div className="project-overview"><div className="overview-icon"><Sparkles size={30} /></div><span className="eyebrow">{t("workspace.spaceEyebrow")}</span><h1>{activeProject?.name ?? t("workspace.overviewTitle")}</h1>{sessions.data?.length ? <><p>{t("workspace.overviewDescription")}</p><button className="primary-button" onClick={() => { const item = sessions.data?.[0]; if (item) selectSession(item.project_id, item.id); }}>{t("workspace.openSession")} <ChevronRight size={17} /></button></> : <p>{t(sessions.isLoading ? "workspace.loadingSessions" : activeProject ? "workspace.noProjectSessions" : "workspace.noProjects")}</p>}</div>}
      </Panel>
      {agentVisible && <><Separator className="resize-handle" /><Panel defaultSize="340px" minSize="280px" maxSize="520px" className="agent-panel-wrapper"><AgentPanel run={sessionId ? run : emptyRun} usage={usage.data} savedArtifacts={artifacts.data ?? []} onCancel={sessionId && visibleRunId ? () => { void cancel(); } : undefined} onApproval={sessionId && visibleRunId ? (id, decision) => { void decideApproval(id, decision); } : undefined} /></Panel></>}
    </Group></div>
  </div>;
}
