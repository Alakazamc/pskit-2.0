import * as Popover from "@radix-ui/react-popover";
import { Atom, BarChart3, Beaker, BookOpen, Briefcase, Code2, Cpu, Database, Dna, FlaskConical, Folder, Globe, GraduationCap, Layers3, Microscope, Music, Network, Palette, Pencil, Scale, Sparkles, Stethoscope, Terminal, Wrench } from "lucide-react";
import { useState } from "react";
import type { Project } from "../../api/types";
import { useLanguage } from "../../i18n/LanguageProvider";
import { useWorkspacePortalContainer } from "../../hooks/useWorkspacePortalContainer";

export type ProjectIconId = NonNullable<Project["icon"]>;

const icons = [
  { id: "folder", glyph: Folder, zh: "文件夹", en: "Folder" },
  { id: "flask", glyph: FlaskConical, zh: "烧瓶", en: "Flask" },
  { id: "atom", glyph: Atom, zh: "原子", en: "Atom" },
  { id: "dna", glyph: Dna, zh: "DNA", en: "DNA" },
  { id: "microscope", glyph: Microscope, zh: "显微镜", en: "Microscope" },
  { id: "beaker", glyph: Beaker, zh: "烧杯", en: "Beaker" },
  { id: "book", glyph: BookOpen, zh: "书本", en: "Book" },
  { id: "database", glyph: Database, zh: "数据库", en: "Database" },
  { id: "cpu", glyph: Cpu, zh: "芯片", en: "Chip" },
  { id: "network", glyph: Network, zh: "网络", en: "Network" },
  { id: "sparkles", glyph: Sparkles, zh: "星光", en: "Sparkles" },
  { id: "layers", glyph: Layers3, zh: "图层", en: "Layers" },
  { id: "graduation", glyph: GraduationCap, zh: "学术", en: "Graduation" },
  { id: "pencil", glyph: Pencil, zh: "铅笔", en: "Pencil" },
  { id: "code", glyph: Code2, zh: "代码", en: "Code" },
  { id: "terminal", glyph: Terminal, zh: "终端", en: "Terminal" },
  { id: "music", glyph: Music, zh: "音乐", en: "Music" },
  { id: "palette", glyph: Palette, zh: "调色板", en: "Palette" },
  { id: "stethoscope", glyph: Stethoscope, zh: "听诊器", en: "Stethoscope" },
  { id: "briefcase", glyph: Briefcase, zh: "公文包", en: "Briefcase" },
  { id: "chart", glyph: BarChart3, zh: "图表", en: "Chart" },
  { id: "scale", glyph: Scale, zh: "天平", en: "Scale" },
  { id: "globe", glyph: Globe, zh: "地球", en: "Globe" },
  { id: "wrench", glyph: Wrench, zh: "扳手", en: "Wrench" },
] as const;

export function suggestedProjectIcon(projects: Project[]): ProjectIconId {
  const used = new Set(projects.map((project) => project.icon ?? "folder"));
  return icons.slice(1).find(({ id }) => !used.has(id))?.id ?? icons[projects.length % icons.length].id;
}

export function ProjectIconGlyph({ icon, size = 18 }: { icon?: Project["icon"]; size?: number }) {
  const found = icons.find(({ id }) => id === icon) ?? icons[0];
  const Glyph = found.glyph;
  return <Glyph size={size} strokeWidth={1.8} aria-hidden="true" data-project-icon={found.id} />;
}

export function ProjectIconPicker({ value, onChange }: { value: ProjectIconId; onChange: (icon: ProjectIconId) => void }) {
  const { language, t } = useLanguage();
  const [query, setQuery] = useState("");
  const results = icons.filter(({ id, zh, en }) => `${id} ${zh} ${en}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  return <div className="mono-project-icon-chooser">
    <input type="search" aria-label={t("project.searchIcons")} placeholder={t("project.searchIcons")} value={query} onChange={(event) => setQuery(event.target.value)} />
    <div className="mono-project-icon-picker" role="group" aria-label={t("project.icon")}>
      {results.map(({ id, glyph: Glyph, zh, en }) => <button key={id} type="button" className="mono-project-icon-option" aria-label={language === "zh" ? zh : en} title={language === "zh" ? zh : en} aria-pressed={value === id} onClick={() => onChange(id)}><Glyph size={20} strokeWidth={1.8} aria-hidden="true" /></button>)}
    </div>
    {results.length === 0 && <p className="mono-project-icon-empty">{t("project.noIcons")}</p>}
  </div>;
}

export function ProjectIconPopover({ value, onChange }: { value: ProjectIconId; onChange: (icon: ProjectIconId) => void }) {
  const { t } = useLanguage();
  const portalContainer = useWorkspacePortalContainer();
  const [open, setOpen] = useState(false);
  return <Popover.Root open={open} onOpenChange={setOpen}>
    <Popover.Trigger asChild><button type="button" className="mono-project-icon-trigger" aria-label={t("project.selectIcon")} title={t("project.selectIcon")}><ProjectIconGlyph icon={value} size={21} /></button></Popover.Trigger>
    <Popover.Portal container={portalContainer}><Popover.Content className="mono-project-icon-popover" side="bottom" align="start" sideOffset={9} collisionPadding={12}><ProjectIconPicker value={value} onChange={(icon) => { onChange(icon); setOpen(false); }} /></Popover.Content></Popover.Portal>
  </Popover.Root>;
}
