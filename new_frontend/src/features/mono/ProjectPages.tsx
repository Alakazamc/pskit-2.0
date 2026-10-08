import * as Dialog from "@radix-ui/react-dialog";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Database, Folder, Plus, Settings2, Sparkles, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import type { CatalogItem, Project, ProjectIcon, ProjectSkillSettings, ResearchApi, Session } from "../../api/types";
import { useLanguage } from "../../i18n/LanguageProvider";
import { useWorkspacePortalContainer } from "../../hooks/useWorkspacePortalContainer";
import { ProjectIconGlyph, ProjectIconPicker, ProjectIconPopover, suggestedProjectIcon } from "./ProjectIcon";
import { projectPath, projectSessionPath } from "./sessionPaths";
import { CatalogCard } from "../../components/catalog/CatalogCard";

export function ProjectIndex({ projects }: { projects: Project[] }) {
  const { t } = useLanguage();
  return <div className="mono-page-scroll"><div className="mono-page-content">
    <div className="catalog-entry-grid">{projects.map((project) => <CatalogCard key={project.id} title={project.name} description={project.description} icon={<ProjectIconGlyph icon={project.icon} />} to={projectPath(project.id)} />)}</div>
    {projects.length === 0 && <div className="mono-empty-panel"><Folder size={24} /><h2>{t("project.empty")}</h2><p>{t("project.emptyDescription")}</p></div>}
  </div></div>;
}

export function CreateProjectDialog({ api, userId, open, onOpenChange }: {
  api: ResearchApi;
  userId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useLanguage();
  const portalContainer = useWorkspacePortalContainer();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [icon, setIcon] = useState<ProjectIcon>("flask");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (open) setIcon(suggestedProjectIcon(queryClient.getQueryData<Project[]>(["projects", userId]) ?? []));
  }, [open, queryClient, userId]);
  const setOpen = (value: boolean) => {
    if (!value) { setName(""); setDescription(""); setError(""); }
    onOpenChange(value);
  };
  const create = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!name.trim() || busy) return;
    setBusy(true); setError("");
    try {
      const project = await api.createProject(name.trim(), description.trim(), icon);
      await queryClient.invalidateQueries({ queryKey: ["projects", userId] });
      setOpen(false);
      navigate(projectPath(project.id));
    } catch { setError(t("project.createFailed")); }
    finally { setBusy(false); }
  };
  return <Dialog.Root open={open} onOpenChange={setOpen}><Dialog.Portal container={portalContainer}>
    <Dialog.Overlay className="mono-dialog-overlay" />
    <Dialog.Content className="mono-dialog-content mono-create-project-dialog" aria-describedby="create-project-description">
      <div className="mono-dialog-heading"><Dialog.Title>{t("project.new")}</Dialog.Title><Dialog.Close aria-label={t("project.close")}><X size={18} /></Dialog.Close></div>
      <Dialog.Description id="create-project-description" className="sr-only">{t("project.visibility")}</Dialog.Description>
      <form onSubmit={create} className="mono-form">
        <div className="mono-project-name-control"><label htmlFor="mono-project-name">{t("project.name")}</label><div className="mono-project-name-field"><ProjectIconPopover value={icon} onChange={setIcon} /><input id="mono-project-name" autoFocus value={name} onChange={(event) => setName(event.target.value)} maxLength={120} required placeholder={t("project.nameExample")} /></div></div>
        <label>{t("project.optionalDescription")}<textarea value={description} onChange={(event) => setDescription(event.target.value)} maxLength={500} rows={3} placeholder={t("project.descriptionExample")} /></label>
        {error && <p role="alert" className="mono-form-error">{error}</p>}
        <div className="mono-dialog-actions"><Dialog.Close className="mono-button" type="button">{t("project.cancel")}</Dialog.Close><button className="mono-button primary" disabled={busy || !name.trim()} type="submit">{t("project.create")}</button></div>
      </form>
    </Dialog.Content>
  </Dialog.Portal></Dialog.Root>;
}

export function ProjectDetail({ api, project, sessions, skills, userId }: { api: ResearchApi; project: Project; sessions: Session[]; skills: CatalogItem[]; userId: string }) {
  const { t } = useLanguage();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [iconOpen, setIconOpen] = useState(false);
  const settings = useQuery({ queryKey: ["project-skills", userId, project.id], queryFn: () => api.getProjectSkills(project.id) });
  const toolRuns = useQuery({ queryKey: ["tool-runs", userId], queryFn: () => api.getToolRuns() });
  const defaults = settings.data?.default_skill_ids ?? [];
  return <div className="mono-page-scroll"><div className="mono-page-content">
    <div className="mono-project-toolbar"><div className="mono-project-identity"><button type="button" className="mono-project-icon-edit" aria-label={t("project.changeIcon")} title={t("project.changeIcon")} onClick={() => setIconOpen(true)}><ProjectIconGlyph icon={project.icon} size={24} /></button>{project.description && <p>{project.description}</p>}</div><Link className="mono-button primary" to={`${projectPath(project.id)}/new`}><Plus size={17} />{t("project.newChat")}</Link></div>
    <section className="mono-panel"><div className="mono-panel-heading"><div><Sparkles size={18} /><h2>{t("project.defaultSkills")}</h2></div><button className="mono-text-button" disabled={!settings.data} onClick={() => setSettingsOpen(true)}><Settings2 size={16} />{t("project.manage")}</button></div><p>{t("project.defaultSkillsDescription")}</p>{settings.isError && <p role="alert" className="mono-form-error">{t("project.skillsLoadFailed")}</p>}<div className="mono-chip-row">{defaults.length ? defaults.map((id) => <span className="mono-chip" key={id}>✦ {skills.find((item) => item.id === id)?.name ?? id}</span>) : <span className="mono-muted">{t("project.noDefaultSkills")}</span>}</div></section>
    <section className="mono-panel"><div className="mono-panel-heading"><div><Folder size={18} /><h2>{t("project.chats")}</h2></div><span className="mono-muted">{t("project.count", { count: sessions.length })}</span></div>{sessions.length ? <div className="mono-list">{sessions.map((session) => <Link key={session.id} to={projectSessionPath(project.id, session.id)}><span>{session.title}</span><small>{t(session.status === "running" ? "project.running" : "project.openChat")}</small><ArrowRight size={16} /></Link>)}</div> : <p>{t("project.noChats")}</p>}</section>
    <section className="mono-panel"><div className="mono-panel-heading"><div><Database size={18} /><h2>{t("project.toolResults")}</h2></div><Link className="mono-text-button" to="/tools">{t("tools.title")} <ArrowRight size={15} /></Link></div>{toolRuns.isError && <p role="alert" className="mono-form-error">{t("project.resultsLoadFailed")}</p>}{toolRuns.data?.some((run) => run.project_id === project.id) ? <div className="mono-list">{toolRuns.data.filter((run) => run.project_id === project.id).map((run) => <div className="mono-project-result" key={run.id}><strong>{run.title}</strong><small>{new Date(run.created_at).toLocaleString()}</small><details><summary>{t("project.viewResult")}</summary><pre>{JSON.stringify(run.result, null, 2)}</pre></details></div>)}</div> : !toolRuns.isError && <p>{t("project.noResults")}</p>}</section>
    <SkillSettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} api={api} project={project} skills={skills} userId={userId} initial={settings.data} />
    <ProjectIconDialog open={iconOpen} onOpenChange={setIconOpen} api={api} project={project} userId={userId} />
  </div></div>;
}

function ProjectIconDialog({ open, onOpenChange, api, project, userId }: { open: boolean; onOpenChange: (open: boolean) => void; api: ResearchApi; project: Project; userId: string }) {
  const { t } = useLanguage();
  const portalContainer = useWorkspacePortalContainer();
  const queryClient = useQueryClient();
  const [icon, setIcon] = useState<ProjectIcon>(project.icon ?? "folder");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { if (open) { setIcon(project.icon ?? "folder"); setError(""); } }, [open, project.icon]);
  const save = async () => {
    if (busy) return;
    setBusy(true); setError("");
    try {
      await api.setProjectIcon(project.id, icon);
      await queryClient.invalidateQueries({ queryKey: ["projects", userId] });
      onOpenChange(false);
    } catch { setError(t("project.saveFailed")); }
    finally { setBusy(false); }
  };
  return <Dialog.Root open={open} onOpenChange={onOpenChange}><Dialog.Portal container={portalContainer}><Dialog.Overlay className="mono-dialog-overlay" /><Dialog.Content className="mono-dialog-content"><div className="mono-dialog-heading"><Dialog.Title>{t("project.icon")}</Dialog.Title><Dialog.Close aria-label={t("project.close")}><X size={18} /></Dialog.Close></div><Dialog.Description className="sr-only">{t("project.chooseIcon")}</Dialog.Description><ProjectIconPicker value={icon} onChange={setIcon} />{error && <p role="alert" className="mono-form-error">{error}</p>}<div className="mono-dialog-actions"><Dialog.Close className="mono-button" type="button">{t("project.cancel")}</Dialog.Close><button className="mono-button primary" disabled={busy || icon === (project.icon ?? "folder")} onClick={() => void save()}>{t("project.saveIcon")}</button></div></Dialog.Content></Dialog.Portal></Dialog.Root>;
}

function SkillSettingsDialog({ open, onOpenChange, api, project, skills, userId, initial }: { open: boolean; onOpenChange: (open: boolean) => void; api: ResearchApi; project: Project; skills: CatalogItem[]; userId: string; initial?: ProjectSkillSettings }) {
  const { t } = useLanguage();
  const portalContainer = useWorkspacePortalContainer();
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<string[]>([]);
  const [defaults, setDefaults] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const reset = () => { setSelected(initial?.skill_ids ?? []); setDefaults(initial?.default_skill_ids ?? []); setError(""); };
  const save = async () => {
    setBusy(true); setError("");
    try {
      await api.setProjectSkills(project.id, { skill_ids: selected, default_skill_ids: defaults });
      await queryClient.invalidateQueries({ queryKey: ["project-skills", userId, project.id] });
      onOpenChange(false);
    } catch { setError(t("project.saveFailed")); }
    finally { setBusy(false); }
  };
  const availableSkills = skills.filter((skill) => skill.available !== false);
  return <Dialog.Root open={open} onOpenChange={(value) => { if (value) reset(); onOpenChange(value); }}><Dialog.Portal container={portalContainer}><Dialog.Overlay className="mono-dialog-overlay" /><Dialog.Content className="mono-dialog-content" aria-describedby="skill-dialog-description"><div className="mono-dialog-heading"><Dialog.Title>{t("project.skillsTitle")}</Dialog.Title><Dialog.Close aria-label={t("project.close")}><X size={18} /></Dialog.Close></div><Dialog.Description id="skill-dialog-description">{t("project.skillsDescription")}</Dialog.Description><div className="mono-skill-options">{availableSkills.map((skill) => { const enabled = selected.includes(skill.id); const isDefault = defaults.includes(skill.id); return <div className="mono-skill-option" key={skill.id}><div><strong>{skill.name}</strong><small>{skill.description}</small></div><label><input type="checkbox" checked={enabled} onChange={() => { setSelected(enabled ? selected.filter((id) => id !== skill.id) : [...selected, skill.id]); if (enabled) setDefaults(defaults.filter((id) => id !== skill.id)); }} />{t("project.includeSkill")}</label><label><input type="checkbox" checked={isDefault} disabled={!enabled || (!isDefault && defaults.length >= 3)} onChange={() => setDefaults(isDefault ? defaults.filter((id) => id !== skill.id) : [...defaults, skill.id])} />{t("project.enableDefault")}</label></div>; })}{availableSkills.length === 0 && <p className="mono-muted">{t("project.noSkills")}</p>}</div>{error && <p role="alert" className="mono-form-error">{error}</p>}<div className="mono-dialog-actions"><span className="mono-muted">{t("project.defaultCount", { count: defaults.length })}</span><button className="mono-button primary" disabled={busy} onClick={() => void save()}>{t("project.saveSettings")}</button></div></Dialog.Content></Dialog.Portal></Dialog.Root>;
}

export function MoveChatDialog({ open, onOpenChange, api, session, projects, userId, onMoved }: { open: boolean; onOpenChange: (open: boolean) => void; api: ResearchApi; session: Session; projects: Project[]; userId: string; onMoved: (projectId: string) => void }) {
  const { t } = useLanguage();
  const portalContainer = useWorkspacePortalContainer();
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const move = async (projectId: string) => {
    setBusy(true); setError("");
    try {
      const moved = await api.moveSession(session.id, projectId, session.project_id === `project-${userId}` ? null : session.project_id);
      queryClient.setQueryData(["session", userId, session.id], moved);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["sessions", userId, session.project_id] }),
        queryClient.invalidateQueries({ queryKey: ["sessions", userId, projectId] }),
      ]);
      onOpenChange(false); onMoved(projectId);
    } catch { setError(t("project.moveFailed")); }
    finally { setBusy(false); }
  };
  return <Dialog.Root open={open} onOpenChange={onOpenChange}><Dialog.Portal container={portalContainer}><Dialog.Overlay className="mono-dialog-overlay" /><Dialog.Content className="mono-dialog-content" aria-describedby="move-dialog-description"><div className="mono-dialog-heading"><Dialog.Title>{t("project.moveTitle")}</Dialog.Title><Dialog.Close aria-label={t("project.close")}><X size={18} /></Dialog.Close></div><Dialog.Description id="move-dialog-description">{t("project.moveDescription", { title: session.title })}</Dialog.Description><div className="mono-move-list">{projects.filter((project) => project.id !== session.project_id).map((project) => <button key={project.id} disabled={busy} onClick={() => void move(project.id)}><ProjectIconGlyph icon={project.icon} />{project.name}<ArrowRight size={16} /></button>)}{projects.filter((project) => project.id !== session.project_id).length === 0 && <p className="mono-muted">{t("project.noMoveTargets")}</p>}</div>{error && <p role="alert" className="mono-form-error">{error}</p>}</Dialog.Content></Dialog.Portal></Dialog.Root>;
}
