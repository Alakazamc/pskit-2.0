import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import type { Message, ResearchApi } from "../../api/types";

type HistoryMeta = { initialLoaded: boolean; nextBefore: string | null; acceptedPending: string[] };
const emptyMeta = (): HistoryMeta => ({ initialLoaded: false, nextBefore: null, acceptedPending: [] });
const metaKey = (userId: string, sessionId: string | null) => ["message-history", userId, sessionId];

/** Keep the existing messages cache contract for optimistic sends and other consumers. */
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

export function acceptPendingMessage(client: QueryClient, userId: string, sessionId: string, pendingId: string): void {
  client.setQueryData<HistoryMeta>(metaKey(userId, sessionId), (meta = emptyMeta()) => ({
    ...meta, acceptedPending: [...meta.acceptedPending, pendingId],
  }));
}

export function useMessageHistory(api: ResearchApi, userId: string, sessionId: string | null, projectId: string | null) {
  const client = useQueryClient();
  const queryKey = ["messages", userId, sessionId];
  const metadataKey = metaKey(userId, sessionId);
  const scope = JSON.stringify(queryKey);
  const guard = useRef<string | null>(null);
  const [loading, setLoading] = useState<{ scope: string; busy: boolean; failed: boolean } | null>(null);
  const metadata = useQuery({ queryKey: metadataKey, queryFn: async () => emptyMeta(), initialData: emptyMeta, enabled: false });
  const messages = useQuery({
    queryKey, enabled: Boolean(sessionId),
    queryFn: async ({ signal }) => {
      // Only remove optimistic messages accepted before this fetch started. A refresh
      // already in flight while POST is pending must retain the submitted message.
      const accepted = client.getQueryData<HistoryMeta>(metadataKey)?.acceptedPending ?? [];
      const page = api.getMessagePage
        ? await api.getMessagePage(sessionId!, projectId, { limit: 50 })
        : { items: await api.getMessages(sessionId!, projectId), next_before: null };
      if (signal.aborted) throw new DOMException("History refresh cancelled", "AbortError");
      const previous = client.getQueryData<Message[]>(queryKey) ?? [];
      const cached = previous.filter((message) => !accepted.includes(message.id));
      const merged = mergeMessageHistory(cached, page.items);
      const cachedIds = new Set(cached.map((message) => message.id));
      // Another device can append more than one page while this conversation is
      // inactive. If the latest page no longer overlaps, its cursor must remain
      // available to recover the interval before it, including cached overlaps.
      const latestGap = cached.length > 0 && page.items.length > 0
        && !page.items.some((message) => cachedIds.has(message.id));
      client.setQueryData<HistoryMeta>(metadataKey, (meta = emptyMeta()) => ({
        ...meta, initialLoaded: true,
        nextBefore: meta.initialLoaded && !latestGap ? meta.nextBefore : page.next_before,
        acceptedPending: meta.acceptedPending.filter((id) => !accepted.includes(id)),
      }));
      return merged;
    },
  });
  const loadEarlier = async (): Promise<void> => {
    const before = client.getQueryData<HistoryMeta>(metadataKey)?.nextBefore;
    if (!sessionId || !api.getMessagePage || !before || guard.current === scope) return;
    guard.current = scope;
    setLoading({ scope, busy: true, failed: false });
    try {
      const page = await api.getMessagePage(sessionId, projectId, { limit: 50, before });
      client.setQueryData<Message[]>(queryKey, (previous = []) => mergeMessageHistory(previous, page.items, "earlier", before));
      client.setQueryData<HistoryMeta>(metadataKey, (meta = emptyMeta()) => ({
        ...meta, nextBefore: meta.nextBefore === before ? page.next_before : meta.nextBefore,
      }));
      setLoading((current) => current?.scope === scope ? { scope, busy: false, failed: false } : current);
    } catch {
      setLoading((current) => current?.scope === scope ? { scope, busy: false, failed: true } : current);
    } finally { if (guard.current === scope) guard.current = null; }
  };
  return { ...messages, hasEarlier: Boolean(api.getMessagePage && metadata.data.nextBefore),
    loadingEarlier: loading?.scope === scope && loading.busy,
    earlierFailed: loading?.scope === scope && loading.failed, loadEarlier };
}
