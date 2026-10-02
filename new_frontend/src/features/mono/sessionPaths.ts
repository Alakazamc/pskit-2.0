export { projectIdFromRouteKey, projectRouteKey } from "../../api/routeKeys";

export function projectPath(projectId: string): string {
  return `/p/${encodeURIComponent(projectId)}`;
}

export function personalSessionPath(sessionId: string): string {
  return `/session/${encodeURIComponent(sessionId)}`;
}

export function projectSessionPath(projectId: string, sessionId: string): string {
  return `${projectPath(projectId)}/c/${encodeURIComponent(sessionId)}`;
}

export function sessionPath(projectId: string, personalProjectId: string, sessionId: string): string {
  return projectId === personalProjectId
    ? personalSessionPath(sessionId)
    : projectSessionPath(projectId, sessionId);
}
