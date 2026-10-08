import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect } from "react";
import type { AdminMe, AdminPage } from "../../api/admin";
import type { ResearchApi } from "../../api/types";
import { ApiError } from "../../api/http";

export const adminKey = (userId: string) => ["admin", userId] as const;
export const adminMeKey = (userId: string) => [...adminKey(userId), "me"] as const;
export const adminSections = [
  ["models", "models:read", "admin.models"],
  ["services", "services:read", "admin.services"],
  ["tool-products", "services:read", "admin.toolProducts"],
  ["skills", "services:read", "admin.skills"],
  ["users", "quotas:read", "admin.users"],
  ["jobs", "jobs:read", "admin.jobs"],
  ["sandboxes", "sandboxes:read", "admin.sandboxes"],
  ["usage", "usage:read", "admin.usage"],
  ["audit", "audit:read", "admin.audit"],
] as const;

export const AdminContext = createContext<{ api: ResearchApi; me: AdminMe } | null>(null);
export function useAdmin() {
  const context = useContext(AdminContext);
  if (!context) throw new Error("Admin provider is missing");
  return context;
}

export function adminErrorKey(error: unknown) {
  if (error instanceof ApiError) {
    if (error.status === 403) return "admin.forbidden";
    if (error.status === 401) return "error.authRequired";
    if (error.status === 409) return "admin.conflict";
    if (error.status === 422) return "admin.invalid";
  }
  return error instanceof SyntaxError ? "admin.invalidJson" : "admin.unavailable";
}

function useAdminError() {
  const { me } = useAdmin();
  const query = useQueryClient();
  return useCallback((error: unknown) => {
    if (!(error instanceof ApiError) || ![401, 403].includes(error.status)) return;
    query.setQueryData<AdminMe>(adminMeKey(me.user_id), (current) => current ? { ...current, permissions: error.status === 401 ? [] : current.permissions.filter((permission) => permission.endsWith(":read")) } : current);
    const resources = { queryKey: adminKey(me.user_id), predicate: (item: { queryKey: readonly unknown[] }) => item.queryKey[2] !== "me" };
    void query.cancelQueries(resources).then(() => { query.removeQueries(resources); });
    void query.invalidateQueries({ queryKey: adminMeKey(me.user_id) });
  }, [me.user_id, query]);
}

export function useAdminList<T>(resource: string, permission: string, load: (cursor?: string) => Promise<AdminPage<T>>, poll = false) {
  const { me } = useAdmin();
  const onError = useAdminError();
  const query = useInfiniteQuery({ queryKey: [...adminKey(me.user_id), resource], initialPageParam: undefined as string | undefined, queryFn: ({ pageParam }) => load(pageParam), getNextPageParam: (page) => page.next_cursor ?? undefined, enabled: me.permissions.includes(permission), refetchInterval: poll ? 5000 : false, retry: false });
  useEffect(() => { if (query.error) onError(query.error); }, [query.error, onError]);
  return query;
}

export function useAdminMutation<T, V>(action: (value: V) => Promise<T>, onSuccess?: (value: T, variables: V) => void) {
  const { me } = useAdmin();
  const query = useQueryClient();
  const onError = useAdminError();
  return useMutation({ mutationFn: action, onError, onSuccess: async (value, variables) => {
    onSuccess?.(value, variables);
    await query.invalidateQueries({ queryKey: adminKey(me.user_id), predicate: (item) => item.queryKey[2] !== "me" });
  } });
}
