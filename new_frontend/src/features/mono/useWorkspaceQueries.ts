import { useQuery } from "@tanstack/react-query";
import type { ResearchApi, UserIdentity } from "../../api/types";
import { isMarkdownContentReady, loadMarkdownContent } from "../chat/markdownLoader";
import { useMessageHistory } from "../chat/useMessageHistory";

export function useWorkspaceQueries(api: ResearchApi, user: UserIdentity, activeSessionId: string | null, activeProjectId: string | null, isChat: boolean) {
  const personalProjectId = `project-${user.id}`;
  const projects = useQuery({ queryKey: ["projects", user.id], queryFn: api.getProjects });
  const personalSessions = useQuery({ queryKey: ["sessions", user.id, personalProjectId], queryFn: () => api.getSessions(null), refetchInterval: (query) => !query.state.error && query.state.data?.some((session) => session.title_status === "pending") ? 1000 : false });
  const projectSessions = useQuery({ queryKey: ["sessions", user.id, activeProjectId], queryFn: () => api.getSessions(activeProjectId!), enabled: !!activeProjectId, refetchInterval: (query) => !query.state.error && query.state.data?.some((session) => session.title_status === "pending") ? 1000 : false });
  const messages = useMessageHistory(api, user.id, activeSessionId, activeProjectId);
  const markdown = useQuery({ queryKey: ["markdown-renderer"], queryFn: loadMarkdownContent, enabled: isChat, staleTime: Infinity, gcTime: Infinity });
  const markdownReady = markdown.isSuccess || isMarkdownContentReady();
  const skills = useQuery({ queryKey: ["skills", user.id], queryFn: api.getSkills });
  const resources = useQuery({ queryKey: ["resources", user.id], queryFn: api.getResources });
  const models = useQuery({ queryKey: ["models", user.id], queryFn: api.getModels });
  const projectSkillSettings = useQuery({ queryKey: ["project-skills", user.id, activeProjectId], queryFn: () => api.getProjectSkills(activeProjectId!), enabled: !!activeProjectId });
  const visibleProjects = (projects.data ?? []).filter((project) => project.id !== personalProjectId);
  const allSessions = activeProjectId ? projectSessions.data ?? [] : personalSessions.data ?? [];
  const listedSession = allSessions.find((session) => session.id === activeSessionId && session.project_id === (activeProjectId ?? personalProjectId));
  const sessionListPending = activeProjectId ? projectSessions.isPending : personalSessions.isPending;
  const sessionLookup = useQuery({ queryKey: ["session", user.id, activeSessionId], queryFn: () => api.getSession(activeSessionId!, activeProjectId), enabled: !!activeSessionId && !listedSession && !sessionListPending, refetchInterval: (query) => !query.state.error && query.state.data?.title_status === "pending" ? 1000 : false });
  const currentSession = listedSession ?? (sessionLookup.data?.project_id === (activeProjectId ?? personalProjectId) ? sessionLookup.data : undefined);
  return { projects, personalSessions, projectSessions, messages, markdown, markdownReady, skills, resources, models, projectSkillSettings, visibleProjects, sessionListPending, sessionLookup, currentSession };
}
