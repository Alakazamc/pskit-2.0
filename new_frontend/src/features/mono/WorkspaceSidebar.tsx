import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { LayoutGrid, MoreHorizontal, PanelLeftClose, PanelLeftOpen, PenLine, Plus, Search, Sparkles } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import type { Project, ResearchApi, Session, UserIdentity } from "../../api/types";
import { useWorkspacePortalContainer } from "../../hooks/useWorkspacePortalContainer";
import { useLanguage } from "../../i18n/LanguageProvider";
import { AccountMenu } from "./AccountMenu";
import { SearchDialog } from "./SearchDialog";
import { ProjectIconGlyph } from "./ProjectIcon";
import { personalSessionPath, projectPath, projectSessionPath } from "./sessionPaths";

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

export function WorkspaceSidebar({
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
    </>}
    <div className="mono-sidebar-footer"><AccountMenu api={api} user={user} compact={collapsed} onNavigate={onClose} onLogout={onLogout} /></div>
    <SearchDialog open={searchOpen} onOpenChange={setSearchOpen} onNavigate={onClose} api={api} userId={user.id} projects={projects} personalSessions={personalSessions} />
  </aside>;
}

