import { Download, FileText, FolderOpen, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import type { FileRef, Project, Session, UsageSnapshot, UserIdentity } from "../../api/types";
import { useLanguage } from "../../i18n/LanguageProvider";
import { UsageCard } from "../usage/UsageCard";

type SidebarProps = {
  projects: Project[];
  sessions: Session[];
  files: FileRef[];
  selectedProject: string | null;
  selected: string | null;
  user: UserIdentity;
  usage?: UsageSnapshot;
  onSelectProject: (project: string) => void;
  onSelect: (project: string, session: string) => void;
  onCreateProject?: (name: string) => Promise<void>;
  onCreateSession?: (title: string) => Promise<void>;
  onDownloadFile?: (id: string, name: string) => Promise<void>;
  onDeleteFile?: (id: string) => Promise<void>;
};

export function ProjectSidebar({
  projects, sessions, files, selectedProject, selected, user, usage,
  onSelectProject, onSelect, onCreateProject, onCreateSession, onDownloadFile, onDeleteFile,
}: SidebarProps) {
  const { t } = useLanguage();
  const [creating, setCreating] = useState<"project" | "session" | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const [fileError, setFileError] = useState(false);
  const [fileBusy, setFileBusy] = useState<string | null>(null);
  const [confirmFileId, setConfirmFileId] = useState<string | null>(null);
  const open = (kind: "project" | "session") => {
    setCreating(kind);
    setDraft("");
    setError(false);
  };
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const name = draft.trim();
    if (!name || !creating) return;
    setBusy(true);
    setError(false);
    try {
      if (creating === "project") await onCreateProject?.(name);
      else await onCreateSession?.(name);
      setCreating(null);
      setDraft("");
    } catch { setError(true); }
    finally { setBusy(false); }
  };
  const downloadFile = async (file: FileRef) => {
    if (!onDownloadFile) return;
    setFileBusy(file.id);
    setFileError(false);
    try { await onDownloadFile(file.id, file.name); }
    catch { setFileError(true); }
    finally { setFileBusy(null); }
  };
  const deleteFile = async (file: FileRef) => {
    if (confirmFileId !== file.id) { setConfirmFileId(file.id); return; }
    if (!onDeleteFile) return;
    setFileBusy(file.id);
    setFileError(false);
    try { await onDeleteFile(file.id); setConfirmFileId(null); }
    catch { setFileError(true); }
    finally { setFileBusy(null); }
  };

  return <aside className="project-sidebar">
    <header className="sidebar-header"><div className="sidebar-title"><span className="workspace-logo">P</span><div><b>PSKit Research</b><span>{t("sidebar.personal")}</span></div></div></header>
    <div className="sidebar-scroll">
      <div className="sidebar-section-heading"><span>{t("sidebar.workspace")}</span>{onCreateProject && <button type="button" aria-label={t("sidebar.newProject")} onClick={() => open("project")}><Plus size={16} /></button>}</div>
      {projects.map((project) => <button type="button" className={`project-card ${selectedProject === project.id ? "active" : ""}`} key={project.id} aria-current={selectedProject === project.id ? "page" : undefined} onClick={() => onSelectProject(project.id)}><div className="project-icon"><FolderOpen size={17} /></div><div><b>{project.name}</b></div></button>)}
      {creating === "project" && <form className="sidebar-create-form" onSubmit={submit}><label htmlFor="sidebar-create-name">{t("sidebar.projectName")}</label><input id="sidebar-create-name" autoFocus value={draft} maxLength={120} onChange={(event) => setDraft(event.target.value)} required /><button type="submit" disabled={busy}>{t("sidebar.createProject")}</button></form>}
      <div className="sidebar-section-heading session-heading"><span>{t("sidebar.sessions")}</span>{onCreateSession && selectedProject && <button type="button" aria-label={t("sidebar.newSession")} onClick={() => open("session")}><Plus size={16} /></button>}</div>
      <div className="session-list">{sessions.map((session) => <button key={session.id} className={`session-item ${selected === session.id ? "active" : ""}`} onClick={() => onSelect(session.project_id, session.id)}><span className="session-dot" /> <span>{session.title}</span></button>)}{sessions.length === 0 && <div className="sidebar-empty">{t("sidebar.noSessions")}</div>}</div>
      {creating === "session" && <form className="sidebar-create-form" onSubmit={submit}><label htmlFor="sidebar-create-name">{t("sidebar.sessionTitle")}</label><input id="sidebar-create-name" autoFocus value={draft} maxLength={160} onChange={(event) => setDraft(event.target.value)} required /><button type="submit" disabled={busy}>{t("sidebar.createSession")}</button></form>}
      {error && <div className="sidebar-create-error" role="alert">{t("workspace.createFailed")}</div>}
      <div className="sidebar-section-heading files-heading"><span>{t("sidebar.files")}</span></div>
      {files.length ? files.map((file) => <div className="file-item" key={file.id}>
        <FileText size={16} /><span className="file-name" title={file.name}>{file.name}</span><small>{Math.ceil(file.size / 1024)} KB</small>
        {onDownloadFile && <button type="button" className="file-action" aria-label={t("sidebar.downloadFile").replace("{name}", file.name)} disabled={fileBusy === file.id} onClick={() => void downloadFile(file)}><Download size={14} /></button>}
        {onDeleteFile && <button type="button" className="file-action" aria-label={t(confirmFileId === file.id ? "sidebar.confirmDeleteFile" : "sidebar.deleteFile").replace("{name}", file.name)} disabled={fileBusy === file.id} onClick={() => void deleteFile(file)}><Trash2 size={14} /></button>}
      </div>) : <div className="file-item"><FileText size={16} /> {t("sidebar.noFiles")}</div>}
      {fileError && <div className="sidebar-create-error" role="alert">{t("sidebar.fileActionFailed")}</div>}
    </div>
    <div className="sidebar-bottom"><UsageCard usage={usage} /><div className="profile-row"><div className="profile-avatar">{user.name.slice(0, 1).toUpperCase()}</div><div><b>{user.name}</b><small>{user.email}</small></div></div></div>
  </aside>;
}
