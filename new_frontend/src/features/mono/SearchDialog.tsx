import * as Dialog from "@radix-ui/react-dialog";
import { useQueries } from "@tanstack/react-query";
import { MessageCircle, X } from "lucide-react";
import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { Project, ResearchApi, Session } from "../../api/types";
import { useLanguage } from "../../i18n/LanguageProvider";
import { useWorkspacePortalContainer } from "../../hooks/useWorkspacePortalContainer";
import { ProjectIconGlyph } from "./ProjectIcon";
import { personalSessionPath, projectSessionPath } from "./sessionPaths";

type ChatResult = { session: Session; project?: Project };

export function SearchDialog({ open, onOpenChange, onNavigate, api, userId, projects, personalSessions }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onNavigate: () => void;
  api: ResearchApi;
  userId: string;
  projects: Project[];
  personalSessions: Session[];
}) {
  const { t } = useLanguage();
  const portalContainer = useWorkspacePortalContainer();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const projectQueries = useQueries({ queries: projects.map((project) => ({
    queryKey: ["sessions", userId, project.id],
    queryFn: () => api.getSessions(project.id),
    enabled: open,
  })) });
  const allChats: ChatResult[] = [
    ...[...personalSessions].reverse().map((session) => ({ session })),
    ...projects.flatMap((project, index) => [...(projectQueries[index]?.data ?? [])].reverse().map((session) => ({ session, project }))),
  ];
  const normalized = query.trim().toLocaleLowerCase();
  const results = normalized
    ? allChats.filter(({ session, project }) => session.title.toLocaleLowerCase().includes(normalized) || project?.name.toLocaleLowerCase().includes(normalized))
    : allChats.slice(0, 20);
  const loading = projectQueries.some((result) => result.isPending && result.fetchStatus === "fetching");
  const partialFailure = projectQueries.some((result) => result.isError);
  const close = (value: boolean) => { if (!value) setQuery(""); onOpenChange(value); };
  const openChat = ({ session, project }: ChatResult) => {
    close(false);
    onNavigate();
    navigate(project ? projectSessionPath(project.id, session.id) : personalSessionPath(session.id));
  };
  const chatPath = ({ session, project }: ChatResult) => project
    ? projectSessionPath(project.id, session.id)
    : personalSessionPath(session.id);
  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Enter" && event.target === inputRef.current && results[0]) {
      event.preventDefault();
      openChat(results[0]);
      return;
    }
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    const links = Array.from(listRef.current?.querySelectorAll<HTMLAnchorElement>("a") ?? []);
    if (!links.length) return;
    event.preventDefault();
    const current = links.indexOf(document.activeElement as HTMLAnchorElement);
    const next = event.key === "ArrowDown"
      ? current < 0 ? 0 : (current + 1) % links.length
      : current < 0 ? links.length - 1 : (current - 1 + links.length) % links.length;
    links[next].focus();
  };

  return <Dialog.Root open={open} onOpenChange={close}><Dialog.Portal container={portalContainer}>
    <Dialog.Overlay className="mono-dialog-overlay" />
    <Dialog.Content className="mono-search-dialog" onKeyDown={onKeyDown}>
      <Dialog.Title className="sr-only">{t("mono.searchChats")}</Dialog.Title>
      <Dialog.Description className="sr-only">{t("mono.searchDescription")}</Dialog.Description>
      <div className="mono-search-header">
        <input ref={inputRef} type="search" aria-label={t("mono.searchChats")} placeholder={t("mono.searchPlaceholder")} value={query} onChange={(event) => setQuery(event.target.value)} />
        <Dialog.Close className="mono-search-close" aria-label={t("mono.closeSearch")}><X size={20} /></Dialog.Close>
      </div>
      <div className="mono-search-body" ref={listRef}>
        <div className="mono-search-section-title">{t(normalized ? "mono.searchResults" : "mono.recentChats")}</div>
        {results.map((chat) => <a key={`${chat.session.project_id}-${chat.session.id}`} className="mono-search-result" href={chatPath(chat)} onClick={(event) => { event.preventDefault(); openChat(chat); }}>
          <MessageCircle size={20} strokeWidth={1.7} aria-hidden="true" />
          <span className="mono-search-result-title">{chat.session.title}</span>
          {chat.project && <span className="mono-search-result-project"><ProjectIconGlyph icon={chat.project.icon} size={14} />{chat.project.name}</span>}
        </a>)}
        {loading && <p className="mono-search-status" role="status">{t("mono.searchLoading")}</p>}
        {!loading && results.length === 0 && <p className="mono-search-status">{t(normalized ? "mono.noSearchResults" : "mono.noChats")}</p>}
        {partialFailure && <p className="mono-search-status">{t("mono.searchPartialFailure")}</p>}
      </div>
    </Dialog.Content>
  </Dialog.Portal></Dialog.Root>;
}
