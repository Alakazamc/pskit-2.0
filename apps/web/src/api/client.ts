import { createHttpApi } from "./http";
import type { ResearchApi } from "./types";

export const isDemoAuth = import.meta.env.VITE_AUTH_MODE === "demo"
  || (!import.meta.env.VITE_AUTH_MODE && import.meta.env.DEV);

export type AuthenticatedResearchApi = ResearchApi & { setAccessToken: (token: string | null) => void };

export function createApi(): AuthenticatedResearchApi {
  let accessToken: string | null = null;
  let refreshPromise: Promise<string | null> | null = null;
  const api: ResearchApi = createHttpApi({
    baseUrl: import.meta.env.VITE_API_BASE_URL || "/api/v1",
    token: () => isDemoAuth ? window.localStorage.getItem("research_access_token") : accessToken,
    onUnauthorized: isDemoAuth ? undefined : () => {
      if (!refreshPromise) {
        refreshPromise = api.refreshAuth()
          .then((session) => { accessToken = session.access_token; return accessToken; })
          .catch(() => { accessToken = null; return null; })
          .finally(() => { refreshPromise = null; });
      }
      return refreshPromise;
    },
  });
  return Object.assign(api, { setAccessToken: (token: string | null) => { accessToken = token; } });
}
