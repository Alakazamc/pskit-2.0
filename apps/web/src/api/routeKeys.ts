export function projectRouteKey(projectId: string): string {
  return `g-p-${projectId.replace(/^project-/, "")}`;
}

export function projectIdFromRouteKey(key: string): string | null {
  return key.startsWith("g-p-") && key.length > 4 ? `project-${key.slice(4)}` : null;
}
