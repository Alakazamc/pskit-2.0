import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, FileText, FolderInput, LayoutGrid, LogOut, Menu, Moon, MoreHorizontal, PanelLeftClose, PanelLeftOpen, PenLine, Plus, Search, Settings2, Sparkles, Sun } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import type { AuthSessionResponse, Message, MessageRequest, Project, ResearchApi, Session, UserIdentity } from "../../api/types";
import { ApiError } from "../../api/http";
import { useWorkspacePortalContainer } from "../../hooks/useWorkspacePortalContainer";
import { errorTranslationKey } from "../../i18n/errors";
import { useLanguage } from "../../i18n/LanguageProvider";
import { Composer } from "../chat/Composer";
import { completeComposerDraft, conversationDraftScope, moveComposerDraft, readComposerDraft } from "../chat/composerDrafts";
import { Conversation } from "../chat/Conversation";
import { useRunEvents } from "../chat/useRunEvents";
import { CreateProjectDialog, MoveChatDialog, ProjectDetail, ProjectIndex } from "./ProjectPages";
import { personalSessionPath, projectPath, projectSessionPath, sessionPath } from "./sessionPaths";
import { CatalogLibrary } from "../../components/catalog/CatalogLibrary";
import { ToolDirectory } from "./ToolPages";
import { ArtifactsPage } from "./ArtifactsPage";
import { SettingsPage } from "./SettingsPage";
import { UserAvatar } from "../../components/UserAvatar";
import { SearchDialog } from "./SearchDialog";
import { SessionRenameDialog } from "./SessionRenameDialog";
import { ProjectIconGlyph } from "./ProjectIcon";

type Theme = "dark" | "light";

function initialTheme(): Theme {
  return window.localStorage.getItem("pskit-theme") === "light" ? "light" : "dark";
}

function SidebarChatItem({ session, href, selected, nested = false, onClose, onRename, portalContainer }: {
  session: Session;
  href: string;
  selected: boolean;
  nested?: boolean;
  onClose: () => void;
  onRename: (session: Session) => void;
  portalContainer?: HTMLElement;
}) {
  const { t } = useLanguage();
  return <div className={`mono-chat-item ${selected ? "selected" : ""}`}>
    <Link to={href} className={`mono-chat-row ${nested ? "nested" : ""}`} aria-current={selected ? "page" : undefined} onClick={onClose} title={session.title}>{session.title}</Link>
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild><button type="button" className="mono-chat-more" aria-label={t("mono.chatMore", { title: session.title })} title={t("mono.chatMore", { title: session.title })}><MoreHorizontal size={17} aria-hidden="true" /></button></DropdownMenu.Trigger>
      <DropdownMenu.Portal container={portalContainer}><DropdownMenu.Content className="add-menu mono-chat-menu" side="right" align="start" sideOffset={4}>
        <DropdownMenu.Item onSelect={() => onRename(session)}><PenLine size={16} aria-hidden="true" />{t("mono.rename")}</DropdownMenu.Item>
      </DropdownMenu.Content></DropdownMenu.Portal>
    </DropdownMenu.Root>
  </div>;
}

function Sidebar({
  api, user, projects, personalSessions, projectSessions, activeProjectId, activeSessionId,
  pathname, collapsed, onCollapse, onClose, onLogout, onRenameChat, onCreateProject,
}: {
  api: ResearchApi;
  user: UserIdentity;
  projects: Project[];
  personalSessions: Session[];
  projectSessions: Session[];
  activeProjectId: string | null;
  activeSessionId: string | null;
  pathname: string;
  collapsed: boolean;
  onCollapse: () => void;
  onClose: () => void;
  onLogout: () => void;
  onRenameChat: (session: Session) => void;
  onCreateProject: () => void;
}) {
  const { t } = useLanguage();
  const portalContainer = useWorkspacePortalContainer();
  const [searchOpen, setSearchOpen] = useState(false);
  const personalChatPath = (session: Session) => personalSessionPath(session.id);
  return <aside className={`mono-sidebar ${collapsed ? "collapsed" : ""}`} aria-label={t("mono.sidebar")}>
    <div className="mono-sidebar-brand"><Link to="/" onClick={onClose}>PSKit</Link><div className="mono-sidebar-brand-actions"><button type="button" aria-label={t("mono.searchChats")} title={t("mono.searchChats")} onClick={() => setSearchOpen(true)}><Search size={19} /></button><button type="button" aria-label={collapsed ? t("mono.expandSidebar") : t("mono.collapseSidebar")} onClick={onCollapse}>{collapsed ? <PanelLeftOpen size={19} /> : <PanelLeftClose size={19} />}</button></div></div>
    {!collapsed && <>
      <nav className="mono-primary-nav" aria-label={t("mono.mainNavigation")}>
        <Link to="/" className={`mono-nav-row ${pathname === "/" ? "selected" : ""}`} aria-current={pathname === "/" ? "page" : undefined} onClick={onClose}><PenLine size={18} />{t("mono.newChat")}</Link>
        <Link to="/tools" className={`mono-nav-row ${pathname.startsWith("/tools") ? "selected" : ""}`} aria-current={pathname.startsWith("/tools") ? "page" : undefined} onClick={onClose}><LayoutGrid size={18} />{t("tools.title")}</Link>
        <Link to="/artifacts" className={`mono-nav-row ${pathname === "/artifacts" ? "selected" : ""}`} aria-current={pathname === "/artifacts" ? "page" : undefined} onClick={onClose}><FileText size={18} />{t("artifact.title")}</Link>
        <Link to="/skills" className={`mono-nav-row ${pathname === "/skills" ? "selected" : ""}`} aria-current={pathname === "/skills" ? "page" : undefined} onClick={onClose}><Sparkles size={18} />{t("mono.skills")}</Link>
      </nav>
      <div className="mono-sidebar-scroll">
        <div className="mono-section-heading"><Link to="/g" onClick={onClose}>{t("mono.projects")}</Link><button type="button" className="mono-section-action" aria-label={t("mono.addProject")} title={t("mono.addProject")} onClick={onCreateProject}><Plus size={16} aria-hidden="true" /></button></div>
        {projects.map((project) => <div key={project.id}>
          <Link to={projectPath(project.id)} className={`mono-project-row ${activeProjectId === project.id && !activeSessionId ? "selected" : ""}`} aria-current={activeProjectId === project.id && !activeSessionId ? "page" : undefined} onClick={onClose}><ProjectIconGlyph icon={project.icon} /><span>{project.name}</span></Link>
          {activeProjectId === project.id && projectSessions.map((session) => <SidebarChatItem key={session.id} session={session} href={projectSessionPath(project.id, session.id)} selected={activeSessionId === session.id} nested onClose={onClose} onRename={onRenameChat} portalContainer={portalContainer} />)}
        </div>)}
        <div className="mono-section-heading history">{t("mono.recentChats")}</div>
        {personalSessions.map((session) => <SidebarChatItem key={session.id} session={session} href={personalChatPath(session)} selected={activeSessionId === session.id && !activeProjectId} onClose={onClose} onRename={onRenameChat} portalContainer={portalContainer} />)}
        {personalSessions.length === 0 && <div className="mono-sidebar-hint">{t("mono.noChats")}</div>}
      </div>
      <div className="mono-sidebar-footer"><UserAvatar api={api} user={user} /><div><b>{user.name}</b><small>{t("mono.personalSpace")}</small></div><button type="button" title={t("mono.signOut")} aria-label={t("mono.signOut")} onClick={onLogout}><LogOut size={17} /></button></div>
    </>}
    <SearchDialog open={searchOpen} onOpenChange={setSearchOpen} onNavigate={onClose} api={api} userId={user.id} projects={projects} personalSessions={personalSessions} />
  </aside>;
}

export function MonoWorkspace({ api, user, onLogout, onSession, onUserChange = () => undefined }: { api: ResearchApi; user: UserIdentity; onLogout: () => void; onSession?: (session: AuthSessionResponse) => void; onUserChange?: (user: UserIdentity) => void }) {
  const { t } = useLanguage();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const pathname = location.pathname;
  const projectMatch = pathname.match(/^\/p\/([^/]+)(?:\/|$)/);
  const activeProjectId = projectMatch ? decodeURIComponent(projectMatch[1]) : null;
  const personalChatMatch = pathname.match(/^\/session\/([^/]+)\/?$/);
  const projectChatMatch = pathname.match(/^\/p\/[^/]+\/c\/([^/]+)\/?$/);
  const activeSessionId = decodeURIComponent(personalChatMatch?.[1] ?? projectChatMatch?.[1] ?? "") || null;
  const personalProjectId = `project-${user.id}`;
  const draftScope = conversationDraftScope(user.id, activeSessionId, activeProjectId);
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [moveOpen, setMoveOpen] = useState(false);
  const [renameTarget, setRenameTarget] = useState<Session | null>(null);
  const [createProjectOpen, setCreateProjectOpen] = useState(false);
  const [sending, setSending] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const [showUpgradePrompt, setShowUpgradePrompt] = useState(false);
  const [currentRun, setCurrentRun] = useState<{ sessionId: string; runId: string } | null>(null);
  const sendGuard = useRef(false);
  const pendingSend = useRef<{ sessionId: string; body: string; key: string } | null>(null);
  useEffect(() => { window.localStorage.setItem("pskit-theme", theme); document.documentElement.dataset.theme = theme; }, [theme]);
  const projects = useQuery({ queryKey: ["projects", user.id], queryFn: api.getProjects });
  const personalSessions = useQuery({ queryKey: ["sessions", user.id, personalProjectId], queryFn: () => api.getSessions(null) });
  const projectSessions = useQuery({ queryKey: ["sessions", user.id, activeProjectId], queryFn: () => api.getSessions(activeProjectId!), enabled: !!activeProjectId });
  const messages = useQuery({ queryKey: ["messages", user.id, activeSessionId], queryFn: () => api.getMessages(activeSessionId!, activeProjectId), enabled: !!activeSessionId });
  const skills = useQuery({ queryKey: ["skills", user.id], queryFn: api.getSkills });
  const resources = useQuery({ queryKey: ["resources", user.id], queryFn: api.getResources });
  const models = useQuery({ queryKey: ["models", user.id], queryFn: api.getModels });
  const projectSkillSettings = useQuery({ queryKey: ["project-skills", user.id, activeProjectId], queryFn: () => api.getProjectSkills(activeProjectId!), enabled: !!activeProjectId });
  const visibleProjects = (projects.data ?? []).filter((project) => project.id !== personalProjectId);
  const allSessions = activeProjectId ? projectSessions.data ?? [] : personalSessions.data ?? [];
  const listedSession = allSessions.find((session) => session.id === activeSessionId && session.project_id === (activeProjectId ?? personalProjectId));
  const sessionListPending = activeProjectId ? projectSessions.isPending : personalSessions.isPending;
  const sessionLookup = useQuery({ queryKey: ["session", user.id, activeSessionId], queryFn: () => api.getSession(activeSessionId!, activeProjectId), enabled: !!activeSessionId && !listedSession && !sessionListPending });
  const currentSession = listedSession ?? (sessionLookup.data?.project_id === (activeProjectId ?? personalProjectId) ? sessionLookup.data : undefined);
  const visibleRunId = submitting ? null : currentRun?.sessionId === activeSessionId ? currentRun.runId : currentSession?.latest_run_id ?? null;
  const onRunCompleted = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["messages", user.id, activeSessionId] });
    void queryClient.invalidateQueries({ queryKey: ["sessions", user.id, activeProjectId ?? personalProjectId] });
    void queryClient.invalidateQueries({ queryKey: ["usage", user.id] });
    void queryClient.invalidateQueries({ queryKey: ["usage-entries", user.id] });
    void queryClient.invalidateQueries({ queryKey: ["usage-activity", user.id] });
  }, [activeProjectId, activeSessionId, personalProjectId, queryClient, user.id]);
  const run = useRunEvents(api, visibleRunId, onRunCompleted);
  const runActive = (run.status === "running" || run.status === "waiting") ||
    (run.status === "idle" && !!visibleRunId && (
      (currentRun?.sessionId === activeSessionId && currentRun.runId === visibleRunId) ||
      currentSession?.status === "running" || currentSession?.status === "waiting"));
  const cancelRun = async () => {
    if (!visibleRunId) return;
    await api.cancelRun(visibleRunId);
    onRunCompleted();
  };
  const decideApproval = async (approvalId: string, decision: "approved" | "rejected") => {
    if (!visibleRunId) return;
    await api.decideApproval(visibleRunId, approvalId, decision);
    void queryClient.invalidateQueries({ queryKey: ["usage", user.id] });
    void queryClient.invalidateQueries({ queryKey: ["usage-entries", user.id] });
  };
  const isChat = pathname === "/" || !!activeSessionId || /^\/p\/[^/]+\/new$/.test(pathname);
  const send = async (payload: MessageRequest) => {
    if (sendGuard.current || !payload.content.trim()) return false;
    sendGuard.current = true;
    setSending(true);
    setSubmitting(true);
    setSendError(null);
    setShowUpgradePrompt(false);
    let optimistic: { queryKey: string[]; id: string } | undefined;
    const submittedDraft = readComposerDraft(draftScope);
    try {
      const targetProjectId = activeProjectId ?? personalProjectId;
      let targetSessionId = activeSessionId;
      let createdSession = false;
      if (!targetSessionId) {
        const title = payload.content.trim().replace(/\s+/g, " ").slice(0, 48);
        const created = await api.createSession(activeProjectId, title);
        targetSessionId = created.id;
        createdSession = true;
        moveComposerDraft(draftScope, conversationDraftScope(user.id, created.id));
        queryClient.setQueryData(["session", user.id, created.id], created);
      }
      const body = JSON.stringify(payload);
      if (pendingSend.current?.sessionId !== targetSessionId || pendingSend.current.body !== body) {
        pendingSend.current = { sessionId: targetSessionId, body, key: crypto.randomUUID() };
      }
      const queryKey = ["messages", user.id, targetSessionId];
      await queryClient.cancelQueries({ queryKey });
      const message: Message = {
        id: `pending-${pendingSend.current.key}`, session_id: targetSessionId, role: "user",
        created_at: new Date().toISOString(),
        parts: [{ type: "text", text: payload.content },
          ...payload.attachments.map((file) => ({ type: "file" as const, id: file.id, name: file.name }))],
      };
      optimistic = { queryKey, id: message.id };
      queryClient.setQueryData<Message[]>(queryKey, (previous = []) => [...previous, message]);
      if (createdSession) navigate(sessionPath(targetProjectId, personalProjectId, targetSessionId));
      const result = await api.sendMessage(targetSessionId, payload, pendingSend.current.key, activeProjectId);
      if (createdSession) completeComposerDraft(conversationDraftScope(user.id, targetSessionId), submittedDraft);
      pendingSend.current = null;
      setCurrentRun({ sessionId: targetSessionId, runId: result.run_id });
      setSubmitting(false);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["messages", user.id, targetSessionId] }),
        queryClient.invalidateQueries({ queryKey: ["sessions", user.id, targetProjectId] }),
        queryClient.invalidateQueries({ queryKey: ["usage", user.id] }),
        queryClient.invalidateQueries({ queryKey: ["usage-entries", user.id] }),
      ]);
      return true;
    } catch (caught) {
      if (optimistic) {
        const id = optimistic.id;
        queryClient.setQueryData<Message[]>(optimistic.queryKey, (previous) => previous?.filter((message) => message.id !== id));
      }
      const key = errorTranslationKey(caught);
      setSendError(key ? t(key) : t("workspace.sendFailed"));
      setShowUpgradePrompt(Boolean(user.is_anonymous && caught instanceof ApiError
        && ["TOKEN_QUOTA_EXCEEDED", "LOGIN_REQUIRED"].includes(caught.code ?? "")));
      return false;
    } finally {
      setSubmitting(false);
      setSending(false);
      sendGuard.current = false;
    }
  };
  const upload = async (file: File, onProgress: (value: number) => void) => {
    const ref = await api.uploadFile(file);
    onProgress(100);
    await queryClient.invalidateQueries({ queryKey: ["files", user.id] });
    return ref;
  };
  const sidebar = <Sidebar api={api} user={user} projects={visibleProjects} personalSessions={personalSessions.data ?? []} projectSessions={projectSessions.data ?? []} activeProjectId={activeProjectId} activeSessionId={activeSessionId} pathname={pathname} collapsed={collapsed} onCollapse={() => setCollapsed(!collapsed)} onClose={() => setMobileOpen(false)} onLogout={onLogout} onRenameChat={setRenameTarget} onCreateProject={() => { setMobileOpen(false); setCreateProjectOpen(true); }} />;
  const currentProject = visibleProjects.find((project) => project.id === activeProjectId);
  const defaultSkills = (projectSkillSettings.data?.default_skill_ids ?? []).map((id) => (skills.data ?? []).find((skill) => skill.id === id)).filter((skill) => !!skill);
  const headerTitle = (() => {
    if (isChat) return "PSKit";
    if (pathname === "/settings") return t("mono.settings");
    if (pathname === "/g") return t("project.title");
    if (pathname === "/artifacts") return t("artifact.title");
    if (pathname === "/skills") return t("mono.skills");
    if (pathname === "/resources") return t("workspace.resourcesTitle");
    if (/^\/tools(?:\/(?:pdb|structure|runs|run\/[^/]+))?$/.test(pathname)) return t("tools.title");
    if (currentProject && pathname === projectPath(currentProject.id)) return currentProject.name;
    return t("mono.pageNotFound");
  })();
  const renderPage = () => {
    if (activeProjectId === personalProjectId) return <div className="mono-page-scroll"><div className="mono-empty-panel">{isChat && <h2>{t("mono.pageNotFound")}</h2>}<Link to="/">{t("mono.backNewChat")}</Link></div></div>;
    if (isChat) {
      if (activeSessionId && !currentSession) return sessionListPending || sessionLookup.isPending || !!sessionLookup.data
        ? <div className="mono-loading-page" role="status">{t("mono.openingChat")}</div>
        : <div className="mono-page-scroll"><div className="mono-empty-panel"><h2>{t("mono.pageNotFound")}</h2><Link to="/">{t("mono.backNewChat")}</Link></div></div>;
      return <div className="mono-chat-layout">{activeSessionId ? <Conversation messages={messages.data ?? []} run={run} runId={visibleRunId} awaitingEvents={run.status === "idle" && runActive} onCancel={visibleRunId ? cancelRun : undefined} onApproval={decideApproval} /> : <div className="mono-empty-chat"><h1>{t("mono.emptyChatTitle")}</h1><p>{t("mono.emptyChatDescription")}</p></div>}{sendError && <div className="mono-error" role="alert">{sendError}{showUpgradePrompt && <Link to="/settings">{t("guest.upgradeToContinue")}</Link>}</div>}{defaultSkills.length > 0 && <div className="mono-default-skills">{t("mono.defaultSkills")}{defaultSkills.map((skill) => <span className="mono-chip" key={skill.id}>✦ {skill.name}</span>)}</div>}<Composer draftScope={draftScope} onSend={send} onUpload={upload} onStop={visibleRunId ? cancelRun : undefined} runActive={runActive} skills={skills.data ?? []} projectSkillIds={projectSkillSettings.data?.skill_ids ?? []} resources={resources.data ?? []} models={models.data ?? []} disabled={sending} /></div>;
    }
    if (pathname === "/g") return <ProjectIndex projects={visibleProjects} />;
    if (pathname === "/artifacts") return <ArtifactsPage api={api} userId={user.id} />;
    if (currentProject && pathname === projectPath(currentProject.id)) return <ProjectDetail api={api} project={currentProject} sessions={projectSessions.data ?? []} skills={skills.data ?? []} userId={user.id} />;
    if (activeProjectId && projects.isLoading) return <div className="mono-loading-page" role="status">{t("mono.openingProject")}</div>;
    if (pathname === "/tools/runs") return <Navigate to="/tools" replace />;
    if (pathname === "/tools" || pathname === "/tools/pdb" || pathname === "/tools/structure" || /^\/tools\/run\/[^/]+$/.test(pathname)) return <ToolDirectory api={api} userId={user.id} projects={visibleProjects} theme={theme} viewer={pathname === "/tools/structure"} selectedName={pathname === "/tools/pdb" ? "search_pdb" : pathname.startsWith("/tools/run/") ? decodeURIComponent(pathname.split("/")[3]) : undefined} />;
    if (pathname === "/skills" || pathname === "/resources") return <div className="mono-page-scroll"><div className="mono-page-content"><CatalogLibrary key={pathname} kind={pathname === "/skills" ? "skills" : "resources"} items={(pathname === "/skills" ? skills.data : resources.data) ?? []} loading={pathname === "/skills" ? skills.isLoading : resources.isLoading} error={pathname === "/skills" ? skills.isError : resources.isError} /></div></div>;
    if (pathname === "/settings") return <SettingsPage api={api} user={user} theme={theme} onThemeChange={setTheme} onUserChange={onUserChange} onSession={onSession} onLogout={onLogout} />;
    return <div className="mono-page-scroll"><div className="mono-empty-panel"><Link to="/">{t("mono.backNewChat")}</Link></div></div>;
  };
  return <div className={`mono-app ${theme}`} data-theme={theme}>
    <div className="mono-desktop-sidebar">{sidebar}</div>
    {mobileOpen && <div className="mono-mobile-overlay"><button type="button" aria-label={t("mono.closeSidebar")} onClick={() => setMobileOpen(false)} className="mono-mobile-backdrop" />{sidebar}</div>}
    <main className="mono-main">
      <header className="mono-topbar"><div className="mono-topbar-left"><button type="button" className="mono-mobile-menu" aria-label={t("mono.openSidebar")} onClick={() => setMobileOpen(true)}><Menu size={19} /></button>{isChat ? <span>{headerTitle}</span> : <h1>{headerTitle}</h1>}{isChat && <ChevronDown size={15} />}{currentSession && <small>{currentSession.title}</small>}</div><div className="mono-topbar-right">{currentSession && <button className="mono-header-action" onClick={() => setMoveOpen(true)}><FolderInput size={16} /><span>{t("mono.moveToProject")}</span></button>}{pathname === "/g" && <button type="button" className="mono-header-action" onClick={() => setCreateProjectOpen(true)}><Plus size={16} /><span>{t("project.new")}</span></button>}<Link className="mono-header-link" to="/settings" aria-label={t("mono.settings")}><Settings2 size={18} /></Link><button type="button" className="mono-theme-toggle" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} title={t(theme === "dark" ? "theme.switchToLight" : "theme.switchToDark")} aria-label={t(theme === "dark" ? "theme.switchToLight" : "theme.switchToDark")}>{theme === "dark" ? <Sun size={19} aria-hidden="true" /> : <Moon size={19} aria-hidden="true" />}</button></div></header>
      {renderPage()}
    </main>
    {currentSession && <MoveChatDialog open={moveOpen} onOpenChange={setMoveOpen} api={api} session={currentSession} projects={[{ id: personalProjectId, name: t("mono.personalChats"), description: "" }, ...visibleProjects]} userId={user.id} onMoved={(projectId) => navigate(sessionPath(projectId, personalProjectId, currentSession.id), { replace: true })} />}
    <SessionRenameDialog key={renameTarget?.id ?? "closed"} api={api} session={renameTarget} userId={user.id} onClose={() => setRenameTarget(null)} />
    <CreateProjectDialog api={api} userId={user.id} open={createProjectOpen} onOpenChange={setCreateProjectOpen} />
  </div>;
}
