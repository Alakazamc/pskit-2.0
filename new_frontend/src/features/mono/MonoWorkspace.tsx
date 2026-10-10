import { ChevronDown, FolderInput, Menu, Moon, Plus, Sun } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import type { AuthSessionResponse, ResearchApi, Session, UserIdentity } from "../../api/types";
import { ApiError } from "../../api/http";
import { useLanguage } from "../../i18n/LanguageProvider";
import { Composer } from "../chat/Composer";
import { conversationDraftScope, handleComposerSignOut } from "../chat/composerDrafts";
import { Conversation } from "../chat/Conversation";
import { ConversationArtifacts } from "../chat/ConversationArtifacts";
import { CreateProjectDialog, MoveChatDialog, ProjectDetail, ProjectIndex } from "./ProjectPages";
import { projectPath, sessionPath } from "./sessionPaths";
import { CatalogLibrary } from "../../components/catalog/CatalogLibrary";
import { ToolDirectory } from "./ToolPages";
import { ToolPageHeaderProvider } from "./ToolPageHeader";
import { SettingsPage } from "./SettingsPage";
import { SessionRenameDialog } from "./SessionRenameDialog";
import { PageLoading, PageLoadError } from "../../components/PageLoading";

import { WorkspaceSidebar } from "./WorkspaceSidebar";
import { useWorkspaceQueries } from "./useWorkspaceQueries";
import { useConversationLifecycle } from "./useConversationLifecycle";
import { useConversationSend } from "./useConversationSend";

type Theme = "dark" | "light";

function initialTheme(): Theme {
  try { return window.localStorage.getItem("pskit-theme") === "light" ? "light" : "dark"; }
  catch { return "dark"; }
}

export function MonoWorkspace({ api, user, onLogout, onSession, onUserChange = () => undefined }: { api: ResearchApi; user: UserIdentity; onLogout: () => void; onSession?: (session: AuthSessionResponse) => void; onUserChange?: (user: UserIdentity) => void }) {
  const { t } = useLanguage();
  const navigate = useNavigate();
  const location = useLocation();
  const pathname = location.pathname;
  const signOut = useCallback(() => { handleComposerSignOut(user.id); onLogout(); }, [user.id, onLogout]);
  const isToolDetail = /^\/tools\/(?:run\/[^/]+|[^/]+)$/.test(pathname) && pathname !== "/tools/runs";
  const [toolHeaderTarget, setToolHeaderTarget] = useState<HTMLDivElement | null>(null);
  const projectMatch = pathname.match(/^\/p\/([^/]+)(?:\/|$)/);
  const activeProjectId = projectMatch ? decodeURIComponent(projectMatch[1]) : null;
  const personalChatMatch = pathname.match(/^\/session\/([^/]+)\/?$/);
  const projectChatMatch = pathname.match(/^\/p\/[^/]+\/c\/([^/]+)\/?$/);
  const activeSessionId = decodeURIComponent(personalChatMatch?.[1] ?? projectChatMatch?.[1] ?? "") || null;
  const isChat = pathname === "/" || !!activeSessionId || /^\/p\/[^/]+\/new$/.test(pathname);
  const personalProjectId = `project-${user.id}`;
  const draftScope = conversationDraftScope(user.id, activeSessionId, activeProjectId);
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [moveOpen, setMoveOpen] = useState(false);
  const [renameTarget, setRenameTarget] = useState<Session | null>(null);
  const [createProjectOpen, setCreateProjectOpen] = useState(false);
  useEffect(() => { try { window.localStorage.setItem("pskit-theme", theme); } catch { /* Theme remains usable without browser storage. */ } document.documentElement.dataset.theme = theme; }, [theme]);
  const { projects, personalSessions, projectSessions, messages, markdown, markdownReady, skills, resources, models, projectSkillSettings, visibleProjects, sessionListPending, sessionLookup, currentSession } = useWorkspaceQueries(api, user, activeSessionId, activeProjectId, isChat);
  const { sending, submitting, sendError, showUpgradePrompt, currentRun, send, upload } = useConversationSend(api, user, activeSessionId, activeProjectId, personalProjectId, draftScope);
  const { historyRunId, historyRun, visibleRunId, run, runActive, cancelRun, decideApproval } = useConversationLifecycle(api, user.id, activeSessionId, activeProjectId, personalProjectId, currentSession, currentRun, submitting);
  const sidebar = <WorkspaceSidebar api={api} user={user} projects={visibleProjects} personalSessions={personalSessions.data ?? []} projectSessions={projectSessions.data ?? []} activeProjectId={activeProjectId} activeSessionId={activeSessionId} pathname={pathname} collapsed={collapsed} onCollapse={() => setCollapsed(!collapsed)} onClose={() => setMobileOpen(false)} onLogout={signOut} onRenameChat={setRenameTarget} onCreateProject={() => { setMobileOpen(false); setCreateProjectOpen(true); }} />;
  const currentProject = visibleProjects.find((project) => project.id === activeProjectId);
  const defaultSkills = (projectSkillSettings.data?.default_skill_ids ?? []).map((id) => (skills.data ?? []).find((skill) => skill.id === id)).filter((skill) => !!skill);
  const headerTitle = (() => {
    if (isChat) return "PSKit";
    if (pathname === "/settings") return t("mono.settings");
    if (pathname === "/g") return t("project.title");
    if (pathname === "/skills") return t("mono.skills");
    if (pathname === "/resources") return t("workspace.resourcesTitle");
    if (pathname === "/tools" || pathname.startsWith("/tools/")) return t("tools.title");
    if (currentProject && pathname === projectPath(currentProject.id)) return currentProject.name;
    return t("mono.pageNotFound");
  })();
  const retryChat = () => {
    if (messages.isError) void messages.refetch();
    if (sessionLookup.isError) void sessionLookup.refetch();
    if (sessionListPending || (activeProjectId ? projectSessions.isError : personalSessions.isError)) {
      void (activeProjectId ? projectSessions : personalSessions).refetch();
    }
    if (markdown.isError) void markdown.refetch();
    if (historyRun.query.isError) void historyRun.query.refetch();
  };
  const renderPage = () => {
    if (activeProjectId === personalProjectId) return <div className="mono-page-scroll"><div className="mono-empty-panel">{isChat && <h2>{t("mono.pageNotFound")}</h2>}<Link to="/">{t("mono.backNewChat")}</Link></div></div>;
    if (isChat) {
      if (activeSessionId && !currentSession) return sessionListPending || sessionLookup.isFetching
        ? <PageLoading label={t("mono.openingChat")} />
        : sessionLookup.isError && !(sessionLookup.error instanceof ApiError && sessionLookup.error.status === 404)
        ? <PageLoadError message={t("mono.chatLoadFailed")} retryLabel={t("loading.retry")} onRetry={retryChat} />
        : <div className="mono-page-scroll"><div className="mono-empty-panel"><h2>{t("mono.pageNotFound")}</h2><Link to="/">{t("mono.backNewChat")}</Link></div></div>;
      if (activeSessionId && (messages.data === undefined || !markdownReady || (historyRunId && historyRun.query.data === undefined))) {
        return (messages.isError || markdown.isError || (historyRunId && historyRun.query.isError))
          ? <PageLoadError message={t("mono.chatLoadFailed")} retryLabel={t("loading.retry")} onRetry={retryChat} />
          : <PageLoading label={t("mono.openingChat")} />;
      }
      return <div className="mono-chat-layout">{activeSessionId ? <Conversation key={activeSessionId} attachmentImages={{ userId: user.id, loadImage: api.downloadFile }} resourceActions={{ downloadFile: api.downloadFile, downloadArtifact: api.downloadArtifact, getArtifactPreview: api.getArtifactPreview }} messages={messages.data ?? []} hasEarlier={messages.hasEarlier} loadingEarlier={messages.loadingEarlier} earlierFailed={messages.earlierFailed} onLoadEarlier={messages.loadEarlier} run={run} runId={visibleRunId ?? historyRunId} awaitingEvents={run.status === "idle" && runActive} onCancel={visibleRunId ? cancelRun : undefined} onApproval={decideApproval} /> : <div className="mono-empty-chat"><h1>{t("mono.emptyChatTitle")}</h1><p>{t("mono.emptyChatDescription")}</p></div>}{sendError && <div className="mono-error" role="alert">{sendError}{showUpgradePrompt && <Link to="/settings">{t("guest.upgradeToContinue")}</Link>}</div>}{defaultSkills.length > 0 && <div className="mono-default-skills">{t("mono.defaultSkills")}{defaultSkills.map((skill) => <span className="mono-chip" key={skill.id}>✦ {skill.name}</span>)}</div>}<Composer draftScope={draftScope} onSend={send} onUpload={upload} onLoadAttachmentImage={api.downloadFile} onStop={visibleRunId ? cancelRun : undefined} runActive={runActive} skills={skills.data ?? []} projectSkillIds={projectSkillSettings.data?.skill_ids ?? []} resources={resources.data ?? []} models={models.data ?? []} disabled={sending} /></div>;
    }
    if (pathname === "/g") return <ProjectIndex projects={visibleProjects} />;
    if (pathname === "/artifacts") return <Navigate to="/" replace />;
    if (currentProject && pathname === projectPath(currentProject.id)) return <ProjectDetail api={api} project={currentProject} sessions={projectSessions.data ?? []} skills={skills.data ?? []} userId={user.id} />;
    if (activeProjectId && projects.isLoading) return <PageLoading label={t("mono.openingProject")} />;
    if (pathname === "/tools/runs") return <Navigate to="/tools" replace />;
    if (pathname === "/tools" || isToolDetail) return <ToolDirectory api={api} userId={user.id} projects={visibleProjects} theme={theme} viewer={pathname === "/tools/structure"} selectedName={pathname === "/tools/pdb" ? "search_pdb" : pathname.startsWith("/tools/run/") ? decodeURIComponent(pathname.split("/")[3]) : undefined} productSlug={/^\/tools\/[^/]+$/.test(pathname) && !["pdb", "structure", "runs"].includes(pathname.split("/")[2]) ? decodeURIComponent(pathname.split("/")[2]) : undefined} />;
    if (pathname === "/skills" || pathname === "/resources") return <div className="mono-page-scroll"><div className="mono-page-content"><CatalogLibrary key={pathname} api={api} kind={pathname === "/skills" ? "skills" : "resources"} items={(pathname === "/skills" ? skills.data : resources.data) ?? []} loading={pathname === "/skills" ? skills.isLoading : resources.isLoading} error={pathname === "/skills" ? skills.isError : resources.isError} /></div></div>;
    if (pathname === "/settings") return <SettingsPage api={api} user={user} theme={theme} onThemeChange={setTheme} onUserChange={onUserChange} onSession={onSession} onLogout={signOut} />;
    return <div className="mono-page-scroll"><div className="mono-empty-panel"><Link to="/">{t("mono.backNewChat")}</Link></div></div>;
  };
  return <div className={`mono-app ${theme}`} data-theme={theme}>
    <div className="mono-desktop-sidebar">{sidebar}</div>
    {mobileOpen && <div className="mono-mobile-overlay"><button type="button" aria-label={t("mono.closeSidebar")} onClick={() => setMobileOpen(false)} className="mono-mobile-backdrop" />{sidebar}</div>}
    <ToolPageHeaderProvider target={toolHeaderTarget}><main className="mono-main">
      <header className={`mono-topbar${isToolDetail ? " mono-topbar-tool" : ""}`}><div className="mono-topbar-left"><button type="button" className="mono-mobile-menu" aria-label={t("mono.openSidebar")} onClick={() => setMobileOpen(true)}><Menu size={19} /></button>{isToolDetail ? <div className="mono-tool-header-slot" ref={setToolHeaderTarget} /> : isChat ? <span>{headerTitle}</span> : <h1>{headerTitle}</h1>}{isChat && <ChevronDown size={15} />}{currentSession && <small>{currentSession.title}</small>}</div><div className="mono-topbar-right">{isChat && currentSession && <ConversationArtifacts key={currentSession.id} api={api} userId={user.id} sessionId={currentSession.id} sessionTitle={currentSession.title} runActive={runActive} />}{currentSession && <button className="mono-header-action" onClick={() => setMoveOpen(true)}><FolderInput size={16} /><span>{t("mono.moveToProject")}</span></button>}{pathname === "/g" && <button type="button" className="mono-header-action" onClick={() => setCreateProjectOpen(true)}><Plus size={16} /><span>{t("project.new")}</span></button>}<button type="button" className="mono-theme-toggle" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} title={t(theme === "dark" ? "theme.switchToLight" : "theme.switchToDark")} aria-label={t(theme === "dark" ? "theme.switchToLight" : "theme.switchToDark")}>{theme === "dark" ? <Sun size={19} aria-hidden="true" /> : <Moon size={19} aria-hidden="true" />}</button></div></header>
      {renderPage()}
    </main></ToolPageHeaderProvider>
    {currentSession && <MoveChatDialog open={moveOpen} onOpenChange={setMoveOpen} api={api} session={currentSession} projects={[{ id: personalProjectId, name: t("mono.personalChats"), description: "" }, ...visibleProjects]} userId={user.id} onMoved={(projectId) => navigate(sessionPath(projectId, personalProjectId, currentSession.id), { replace: true })} />}
    <SessionRenameDialog key={renameTarget?.id ?? "closed"} api={api} session={renameTarget} userId={user.id} onClose={() => setRenameTarget(null)} />
    <CreateProjectDialog api={api} userId={user.id} open={createProjectOpen} onOpenChange={setCreateProjectOpen} />
  </div>;
}
