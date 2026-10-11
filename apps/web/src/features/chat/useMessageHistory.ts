import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useCallback, useMemo, useRef, useState, useSyncExternalStore } from "react";
import type { Message, ResearchApi } from "../../api/types";

type AcceptedPending = { pendingId: string; authoritativeId: string | null };
type HistoryMeta = { initialLoaded: boolean; nextBefore: string | null; acceptedPending: AcceptedPending[]; optimisticPending: Message[] };
const emptyMeta = (): HistoryMeta => ({ initialLoaded: false, nextBefore: null, acceptedPending: [], optimisticPending: [] });
const metaKey = (userId: string, sessionId: string | null) => ["message-history", userId, sessionId];
const emptyPending: Message[] = [];

function confirmPendingMessages(meta: HistoryMeta, accepted: AcceptedPending[], server: Message[], sessionId: string,
  allowLegacy: boolean): HistoryMeta {
  const serverIds = new Set(server.filter((message) => message.session_id === sessionId && message.role === "user")
    .map((message) => message.id));
  const confirmed = new Set(meta.acceptedPending.filter((pending) => pending.authoritativeId
    ? serverIds.has(pending.authoritativeId) : allowLegacy && accepted.includes(pending)).map((pending) => pending.pendingId));
  return { ...meta, acceptedPending: meta.acceptedPending.filter((pending) => !confirmed.has(pending.pendingId)),
    optimisticPending: meta.optimisticPending.filter((message) => !confirmed.has(message.id)) };
}

/** Merge server pages while keeping the messages cache's array contract. */
export function mergeMessageHistory(previous: Message[], incoming: Message[], placement: "latest" | "earlier" = "latest", before?: string): Message[] {
  // IDs are opaque: equal timestamps must keep the server's ordinal order.
  // Earlier pages precede cached rows; latest pages append without remounting
  // unchanged rows. Stable sorting only normalizes timestamp offsets.
  const previousIndexes = new Map(previous.map((message, index) => [message.id, index]));
  const incomingIds = new Set(incoming.map((message) => message.id));
  const firstShared = incoming.find((message) => previousIndexes.has(message.id));
  const insertion = firstShared ? previousIndexes.get(firstShared.id)!
    : placement === "earlier" ? (before ? previousIndexes.get(before) ?? 0 : 0) : previous.length;
  const ordered = [
    ...previous.slice(0, insertion).filter((message) => !incomingIds.has(message.id)),
    ...incoming,
    ...previous.slice(insertion).filter((message) => !incomingIds.has(message.id)),
  ];
  const byId = new Map(ordered.map((message) => [message.id, message]));
  for (const message of incoming) byId.set(message.id, message);
  return [...byId.values()].sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at));
}

export function addPendingMessage(client: QueryClient, userId: string, sessionId: string, message: Message): void {
  if (message.session_id !== sessionId) return;
  client.setQueryData<HistoryMeta>(metaKey(userId, sessionId), (meta = emptyMeta()) => ({
    ...meta, optimisticPending: mergeMessageHistory(meta.optimisticPending, [message]),
  }));
}

export function removePendingMessage(client: QueryClient, userId: string, sessionId: string, pendingId: string): void {
  client.setQueryData<HistoryMeta>(metaKey(userId, sessionId), (meta = emptyMeta()) => ({
    ...meta, acceptedPending: meta.acceptedPending.filter((pending) => pending.pendingId !== pendingId),
    optimisticPending: meta.optimisticPending.filter((message) => message.id !== pendingId),
  }));
}

export function acceptPendingMessage(client: QueryClient, userId: string, sessionId: string, pendingId: string,
  authoritativeId?: string | null): void {
  const pending = client.getQueryData<HistoryMeta>(metaKey(userId, sessionId))?.optimisticPending.find((message) => message.id === pendingId)
    ?? client.getQueryData<Message[]>(["messages", userId, sessionId])?.find((message) => message.id === pendingId);
  if (!pending || pending.session_id !== sessionId) return;
  const committed = client.getQueryData<Message[]>(["messages", userId, sessionId]) ?? [];
  client.setQueryData<HistoryMeta>(metaKey(userId, sessionId), (meta = emptyMeta()) => confirmPendingMessages({
    ...meta, acceptedPending: [...meta.acceptedPending.filter((accepted) => accepted.pendingId !== pendingId), {
      pendingId, authoritativeId: authoritativeId ?? meta.acceptedPending.find((accepted) => accepted.pendingId === pendingId)?.authoritativeId ?? null,
    }],
    optimisticPending: mergeMessageHistory(meta.optimisticPending, [pending]),
  }, [], committed, sessionId, false));
}

export function useMessageHistory(api: ResearchApi, userId: string, sessionId: string | null, projectId: string | null) {
  const client = useQueryClient();
  const queryKey = ["messages", userId, sessionId];
  const metadataKey = metaKey(userId, sessionId);
  const scope = JSON.stringify(queryKey);
  const guard = useRef<string | null>(null);
  const [loading, setLoading] = useState<{ scope: string; busy: boolean; failed: boolean } | null>(null);
  const metadata = useQuery({ queryKey: metadataKey, queryFn: async () => emptyMeta(), initialData: emptyMeta, enabled: false });
  // Query observer notifications are batched. Local sends need a synchronous
  // snapshot so a cancelled refresh cannot briefly hide their display rows.
  const optimisticPending = useSyncExternalStore(
    useCallback((notify) => client.getQueryCache().subscribe((event) => {
      const key = event.query.queryKey;
      if ((event.type === "updated" || event.type === "removed") && key.length === 3
        && key[0] === "message-history" && key[1] === userId && key[2] === sessionId) notify();
    }), [client, userId, sessionId]),
    useCallback(() => client.getQueryData<HistoryMeta>(metaKey(userId, sessionId))?.optimisticPending ?? emptyPending,
      [client, userId, sessionId]),
  );
  const messages = useQuery({
    queryKey, enabled: Boolean(sessionId),
    queryFn: async ({ signal }) => {
      // Without an authoritative ID, only a fetch started after acceptance may
      // replace a pending send. Exact IDs can be confirmed by any committed fetch.
      const accepted = client.getQueryData<HistoryMeta>(metadataKey)?.acceptedPending ?? [];
      const page = api.getMessagePage
        ? await api.getMessagePage(sessionId!, projectId, { limit: 50 })
        : { items: await api.getMessages(sessionId!, projectId), next_before: null };
      if (signal.aborted) throw new DOMException("History refresh cancelled", "AbortError");
      const previous = client.getQueryData<Message[]>(queryKey) ?? [];
      const pendingIds = new Set(client.getQueryData<HistoryMeta>(metadataKey)?.optimisticPending.map((message) => message.id));
      const cached = previous.filter((message) => !pendingIds.has(message.id));
      const merged = mergeMessageHistory(cached, page.items);
      const cachedIds = new Set(cached.map((message) => message.id));
      // Another device can append more than one page while this conversation is
      // inactive. If the latest page no longer overlaps, its cursor must remain
      // available to recover the interval before it, including cached overlaps.
      const latestGap = cached.length > 0 && page.items.length > 0
        && !page.items.some((message) => cachedIds.has(message.id));
      // Query can still be cancelled between returning from this function and
      // committing its result. Consume acceptance only after that commit.
      const unsubscribe = client.getQueryCache().subscribe((event) => {
        if (event.type !== "updated" || JSON.stringify(event.query.queryKey) !== scope) return;
        if (event.action.type === "error") { dispose(); return; }
        if (event.action.type !== "success" || event.action.manual) return;
        dispose();
        client.setQueryData<HistoryMeta>(metadataKey, (meta = emptyMeta()) => ({
          // Older endpoints have no stable message ID; preserve their accepted
          // send + fresh latest-page compatibility policy.
          ...confirmPendingMessages(meta, accepted, event.query.state.data as Message[], sessionId!, true), initialLoaded: true,
          nextBefore: meta.initialLoaded && !latestGap ? meta.nextBefore : page.next_before,
        }));
      });
      const dispose = () => { unsubscribe(); signal.removeEventListener("abort", dispose); };
      signal.addEventListener("abort", dispose, { once: true });
      return merged;
    },
  });
  const loadEarlier = async (): Promise<void> => {
    const before = client.getQueryData<HistoryMeta>(metadataKey)?.nextBefore;
    if (!sessionId || !api.getMessagePage || !before || guard.current === scope) return;
    guard.current = scope;
    setLoading({ scope, busy: true, failed: false });
    const accepted = client.getQueryData<HistoryMeta>(metadataKey)?.acceptedPending ?? [];
    try {
      const page = await api.getMessagePage(sessionId, projectId, { limit: 50, before });
      const pendingIds = new Set(client.getQueryData<HistoryMeta>(metadataKey)?.optimisticPending.map((message) => message.id));
      const merged = client.setQueryData<Message[]>(queryKey, (previous = []) => mergeMessageHistory(
        previous.filter((message) => !pendingIds.has(message.id)), page.items, "earlier", before,
      )) ?? [];
      client.setQueryData<HistoryMeta>(metadataKey, (meta = emptyMeta()) => ({
        ...confirmPendingMessages(meta, accepted, merged, sessionId, false),
        nextBefore: meta.nextBefore === before ? page.next_before : meta.nextBefore,
      }));
      setLoading((current) => current?.scope === scope ? { scope, busy: false, failed: false } : current);
    } catch {
      setLoading((current) => current?.scope === scope ? { scope, busy: false, failed: true } : current);
    } finally { if (guard.current === scope) guard.current = null; }
  };
  const data = useMemo(() => optimisticPending.length
    ? mergeMessageHistory(messages.data ?? [], optimisticPending)
    : messages.data, [messages.data, optimisticPending]);
  return { ...messages, data, hasEarlier: Boolean(api.getMessagePage && metadata.data.nextBefore),
    loadingEarlier: loading?.scope === scope && loading.busy,
    earlierFailed: loading?.scope === scope && loading.failed, loadEarlier };
}
