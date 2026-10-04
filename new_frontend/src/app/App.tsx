import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { BrowserRouter, Link, Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";
import { createApi, isDemoAuth } from "../api/client";
import type { AuthSessionResponse, UserIdentity } from "../api/types";
import { LoginPage } from "../features/auth/LoginPage";
import { AdminShell } from "../features/admin/AdminShell";
import { MonoWorkspace } from "../features/mono/MonoWorkspace";
import { personalSessionPath, projectIdFromRouteKey, projectPath } from "../features/mono/sessionPaths";
import { LanguageProvider, useLanguage } from "../i18n/LanguageProvider";

export function App() {
  return <LanguageProvider><AppContent /></LanguageProvider>;
}

function LegacyPersonalChatRedirect() {
  const { sessionId } = useParams();
  const { search, hash } = useLocation();
  return <Navigate to={`${personalSessionPath(sessionId ?? "")}${search}${hash}`} replace />;
}

function LegacyProjectRedirect() {
  const { projectKey, "*": rest } = useParams();
  const { search, hash } = useLocation();
  const projectId = projectIdFromRouteKey(projectKey ?? "");
  if (!projectId) return <Navigate to="/g" replace />;
  const suffix = rest ? `/${rest}` : "";
  return <Navigate to={`${projectPath(projectId)}${suffix}${search}${hash}`} replace />;
}

function AuthCallbackStatus({ user }: { user: UserIdentity | null }) {
  const { search } = useLocation();
  const { t } = useLanguage();
  const error = new URLSearchParams(search).get("error");
  if (!error) return <Navigate to={user ? "/" : "/login"} replace />;
  return <main className="auth-callback-status">
    <p role="alert">{t(error === "GOOGLE_IDENTITY_CONFLICT" ? "guest.googleConflict" : "guest.googleFailed")}</p>
    <Link to={user ? "/settings" : "/login"}>{t(user ? "guest.backSettings" : "auth.backToLogin")}</Link>
  </main>;
}

function AppContent() {
  const { t } = useLanguage();
  const api = useMemo(createApi, []);
  const query = useMemo(() => new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 20_000 } } }), []);
  const [user, setUser] = useState<UserIdentity | null>(null);
  const applySession = (session: AuthSessionResponse) => {
    api.setAccessToken(session.access_token);
    if (isDemoAuth) window.localStorage.setItem("research_access_token", session.access_token);
    setUser(session.user);
    void query.invalidateQueries();
  };
  const [restoring, setRestoring] = useState(true);
  useEffect(() => {
    const restore = async () => {
      if (isDemoAuth) {
        const token = window.localStorage.getItem("research_access_token");
        if (window.location.pathname === "/auth/callback") {
          const session = await api.refreshAuth();
          window.localStorage.setItem("research_access_token", session.access_token);
          api.setAccessToken(session.access_token);
          setUser(session.user);
        } else if (token) setUser(await api.getMe());
      } else {
        const session = await api.refreshAuth();
        api.setAccessToken(session.access_token);
        setUser(session.user);
      }
    };
    void restore().catch(() => {
      api.setAccessToken(null);
      window.localStorage.removeItem("research_access_token");
    }).finally(() => setRestoring(false));
  }, [api]);
  if (restoring) return <div role="status">{t("auth.restoring")}</div>;
  return <QueryClientProvider client={query}><BrowserRouter><Routes>
    <Route path="/login" element={user ? <Navigate to="/" replace /> : <LoginPage api={api} onLogin={(identity, token) => { api.setAccessToken(token); setUser(identity); }} />} />
    <Route path="/auth/callback" element={<AuthCallbackStatus user={user} />} />
    <Route path="/admin/:section?" element={user ? <AdminShell api={api} user={user} onUnauthorized={() => { api.setAccessToken(null); window.localStorage.removeItem("research_access_token"); query.clear(); setUser(null); }} /> : <Navigate to="/login" replace />} />
    <Route path="/c/:sessionId" element={<LegacyPersonalChatRedirect />} />
    <Route path="/g/:projectKey/*" element={<LegacyProjectRedirect />} />
    <Route path="/*" element={user ? <MonoWorkspace api={api} user={user} onSession={applySession} onUserChange={(identity) => setUser((current) => current?.id === identity.id ? identity : current)} onLogout={() => {
      if (user.is_anonymous && !window.confirm(t("guest.signOutWarning"))) return;
      const clear = () => { api.setAccessToken(null); window.localStorage.removeItem("research_access_token"); query.clear(); setUser(null); };
      void api.logoutAuth().catch(() => undefined).finally(clear);
    }} /> : <Navigate to="/login" replace />} />
  </Routes></BrowserRouter></QueryClientProvider>;
}
