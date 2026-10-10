import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, expect, it, vi } from "vitest";
import type { Message, ResearchApi } from "../../api/types";
import { acceptPendingMessage, mergeMessageHistory, useMessageHistory } from "./useMessageHistory";

afterEach(cleanup);
const message = (id: string, minute: number): Message => ({ id, session_id: "session-1", role: "user", created_at: `2026-10-10T00:${String(minute).padStart(2, "0")}:00Z`, parts: [{ type: "text", text: id }] });
function setup(api: Partial<ResearchApi>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  const view = renderHook(() => useMessageHistory(api as ResearchApi, "alice", "session-1", null), { wrapper });
  return { ...view, client };
}

it("keeps the legacy array API unchanged when optional pagination is missing", async () => {
  const getMessages = vi.fn().mockResolvedValue([message("m1", 1)]);
  const { result } = setup({ getMessages });
  await waitFor(() => expect(result.current.data).toHaveLength(1));
  expect(getMessages).toHaveBeenCalledWith("session-1", null); expect(result.current.hasEarlier).toBe(false);
});

it("prepends pages and keeps older messages when the latest page refreshes", async () => {
  const getMessagePage = vi.fn()
    .mockResolvedValueOnce({ items: [message("m3", 3), message("m4", 4)], next_before: "m3" })
    .mockResolvedValueOnce({ items: [message("m1", 1), message("m2", 2)], next_before: null })
    .mockResolvedValueOnce({ items: [message("m4", 4), message("m5", 5)], next_before: "m4" });
  const { result } = setup({ getMessagePage });
  await waitFor(() => expect(result.current.hasEarlier).toBe(true));
  await act(() => result.current.loadEarlier());
  expect(getMessagePage).toHaveBeenNthCalledWith(2, "session-1", null, { limit: 50, before: "m3" });
  await act(() => result.current.refetch());
  expect(result.current.data?.map((item) => item.id)).toEqual(["m1", "m2", "m3", "m4", "m5"]);
  expect(result.current.hasEarlier).toBe(false);
});

it("keeps a new latest-page cursor when another device appends beyond the cached interval", async () => {
  const getMessagePage = vi.fn()
    .mockResolvedValueOnce({ items: [message("m1", 1), message("m2", 2)], next_before: null })
    .mockResolvedValueOnce({ items: [message("m5", 5), message("m6", 6)], next_before: "m5" })
    .mockResolvedValueOnce({ items: [message("m1", 1), message("m2", 2), message("m3", 3), message("m4", 4)], next_before: null });
  const { result } = setup({ getMessagePage });
  await waitFor(() => expect(result.current.data).toHaveLength(2));
  expect(result.current.hasEarlier).toBe(false);
  await act(() => result.current.refetch());
  await waitFor(() => expect(result.current.hasEarlier).toBe(true));
  expect(result.current.data?.map((item) => item.id)).toEqual(["m1", "m2", "m5", "m6"]);
  await act(() => result.current.loadEarlier());
  expect(getMessagePage).toHaveBeenLastCalledWith("session-1", null, { limit: 50, before: "m5" });
  expect(result.current.data?.map((item) => item.id)).toEqual(["m1", "m2", "m3", "m4", "m5", "m6"]);
  expect(result.current.hasEarlier).toBe(false);
});

it("does not let an older in-flight page consume a new latest-refresh gap cursor", async () => {
  let finish!: (value: { items: Message[]; next_before: string | null }) => void;
  const getMessagePage = vi.fn()
    .mockResolvedValueOnce({ items: [message("m3", 3), message("m4", 4)], next_before: "m3" })
    .mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }))
    .mockResolvedValueOnce({ items: [message("m7", 7), message("m8", 8)], next_before: "m7" })
    .mockResolvedValueOnce({ items: Array.from({ length: 6 }, (_, index) => message(`m${index + 1}`, index + 1)), next_before: null });
  const { result } = setup({ getMessagePage });
  await waitFor(() => expect(result.current.hasEarlier).toBe(true));
  let loading!: Promise<void>;
  act(() => { loading = result.current.loadEarlier(); });
  await act(() => result.current.refetch());
  await act(async () => { finish({ items: [message("m1", 1), message("m2", 2)], next_before: null }); await loading; });
  expect(result.current.hasEarlier).toBe(true);
  await act(() => result.current.loadEarlier());
  expect(getMessagePage).toHaveBeenLastCalledWith("session-1", null, { limit: 50, before: "m7" });
  expect(result.current.data?.map((item) => item.id)).toEqual(Array.from({ length: 8 }, (_, index) => `m${index + 1}`));
  expect(result.current.hasEarlier).toBe(false);
});

it("keeps the current conversation's loading state when an earlier request from the previous conversation finishes", async () => {
  const resolvers = new Map<string, (value: { items: Message[]; next_before: string | null }) => void>();
  const api = { getMessagePage: vi.fn((sessionId: string, _project: string | null, options: { before?: string }) => options.before
    ? new Promise((resolve) => { resolvers.set(sessionId, resolve); })
    : Promise.resolve({ items: [{ ...message(`${sessionId}-3`, 3), session_id: sessionId }], next_before: `${sessionId}-3` })) };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  const { result, rerender } = renderHook(({ sessionId }) => useMessageHistory(api as unknown as ResearchApi, "alice", sessionId, null), { wrapper, initialProps: { sessionId: "A" } });
  await waitFor(() => expect(result.current.hasEarlier).toBe(true));
  let loadingA!: Promise<void>;
  act(() => { loadingA = result.current.loadEarlier(); });
  rerender({ sessionId: "B" });
  await waitFor(() => expect(result.current.hasEarlier).toBe(true));
  let loadingB!: Promise<void>;
  act(() => { loadingB = result.current.loadEarlier(); });
  await waitFor(() => expect(result.current.loadingEarlier).toBe(true));
  await act(async () => { resolvers.get("A")!({ items: [], next_before: null }); await loadingA; });
  expect(result.current.loadingEarlier).toBe(true);
  await act(async () => { resolvers.get("B")!({ items: [], next_before: null }); await loadingB; });
  expect(result.current.loadingEarlier).toBe(false);
});

it("preserves optimistic messages until an accepted send is followed by a fresh latest-page fetch", async () => {
  const getMessagePage = vi.fn().mockResolvedValue({ items: [message("m1", 1)], next_before: null });
  const { result, client } = setup({ getMessagePage });
  await waitFor(() => expect(result.current.data).toHaveLength(1));
  act(() => { client.setQueryData<Message[]>(["messages", "alice", "session-1"], [message("m1", 1), message("pending-send", 2)]); });
  await act(() => result.current.refetch());
  await waitFor(() => expect(result.current.data?.map((item) => item.id)).toEqual(["m1", "pending-send"]));
  acceptPendingMessage(client, "alice", "session-1", "pending-send");
  getMessagePage.mockResolvedValue({ items: [message("m1", 1), message("persisted-send", 2)], next_before: null });
  await act(() => result.current.refetch());
  await waitFor(() => expect(result.current.data?.map((item) => item.id)).toEqual(["m1", "persisted-send"]));
});

it("merges late latest-page responses with an older page loaded during that request", async () => {
  let finish!: (value: { items: Message[]; next_before: string | null }) => void;
  const getMessagePage = vi.fn()
    .mockResolvedValueOnce({ items: [message("m3", 3)], next_before: "m3" })
    .mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }))
    .mockResolvedValueOnce({ items: [message("m1", 1), message("m2", 2)], next_before: null });
  const { result } = setup({ getMessagePage });
  await waitFor(() => expect(result.current.hasEarlier).toBe(true));
  let refreshing!: Promise<unknown>;
  act(() => { refreshing = result.current.refetch(); });
  await act(() => result.current.loadEarlier());
  await act(async () => { finish({ items: [message("m3", 3), message("m4", 4)], next_before: "m3" }); await refreshing; });
  await waitFor(() => expect(result.current.data?.map((item) => item.id)).toEqual(["m1", "m2", "m3", "m4"]));
  expect(result.current.hasEarlier).toBe(false);
});

it("does not consume accepted optimistic messages when a latest-page refresh is cancelled", async () => {
  let finish!: (value: { items: Message[]; next_before: string | null }) => void;
  const getMessagePage = vi.fn()
    .mockResolvedValueOnce({ items: [message("m1", 1)], next_before: null })
    .mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }))
    .mockResolvedValueOnce({ items: [message("m1", 1), message("persisted-send", 2)], next_before: null });
  const { result, client } = setup({ getMessagePage });
  await waitFor(() => expect(result.current.data).toHaveLength(1));
  act(() => { client.setQueryData<Message[]>(["messages", "alice", "session-1"], [message("m1", 1), message("pending-send", 2)]); });
  acceptPendingMessage(client, "alice", "session-1", "pending-send");
  let refreshing!: Promise<unknown>;
  act(() => { refreshing = result.current.refetch(); });
  await act(() => client.cancelQueries({ queryKey: ["messages", "alice", "session-1"] }));
  await act(async () => { finish({ items: [message("m1", 1)], next_before: null }); await refreshing; });
  expect(result.current.data?.map((item) => item.id)).toEqual(["m1", "pending-send"]);
  await act(() => result.current.refetch());
  await waitFor(() => expect(result.current.data?.map((item) => item.id)).toEqual(["m1", "persisted-send"]));
});

it("keeps the same cursor and existing display messages after a failed older-page fetch", async () => {
  const getMessagePage = vi.fn().mockResolvedValueOnce({ items: [message("m3", 3)], next_before: "m3" }).mockRejectedValueOnce(new Error("unavailable"));
  const { result } = setup({ getMessagePage });
  await waitFor(() => expect(result.current.hasEarlier).toBe(true));
  await act(() => result.current.loadEarlier());
  await waitFor(() => expect(result.current.earlierFailed).toBe(true));
  expect(result.current.data?.[0].id).toBe("m3"); expect(result.current.hasEarlier).toBe(true);
});

it("keeps stable references for unchanged older messages and lets authoritative data replace duplicate IDs", () => {
  const older = message("m1", 1);
  const refreshed = message("m2", 2);
  const result = mergeMessageHistory([older, message("m2", 2)], [refreshed]);
  expect(result[0]).toBe(older); expect(result[1]).toBe(refreshed);
});

it("orders differently formatted timestamp offsets by their actual time", () => {
  const earlier = { ...message("earlier", 2), created_at: "2026-10-10T08:02:00+08:00" };
  const later = message("later", 3);
  expect(mergeMessageHistory([later], [earlier]).map((item) => item.id)).toEqual(["earlier", "later"]);
});

it("keeps a server's user then assistant order when timestamps tie and IDs sort differently", () => {
  const user = message("message-2", 1);
  const assistant = { ...message("message-10", 1), role: "assistant" as const };
  expect(mergeMessageHistory([], [user, assistant])).toEqual([user, assistant]);
});

it("prepends and appends pages without reordering equal-timestamp server rows", () => {
  const rows = ["message-2", "message-10", "message-1", "message-9"].map((id) => message(id, 1));
  const first = mergeMessageHistory([], rows.slice(2));
  const earlier = mergeMessageHistory(first, rows.slice(0, 3), "earlier");
  expect(earlier.map((item) => item.id)).toEqual(rows.map((item) => item.id));
  expect(earlier[3]).toBe(first[1]);
  const next = message("message-0", 1);
  expect(mergeMessageHistory(earlier, [rows[3], next]).map((item) => item.id))
    .toEqual([...rows.map((item) => item.id), next.id]);
});

it("recovers an equal-timestamp middle interval before its known cursor without moving older cached rows", () => {
  const rows = ["message-2", "message-10", "message-1", "message-9", "message-0", "message-3"].map((id) => message(id, 1));
  const cached = [...rows.slice(0, 2), ...rows.slice(4)];
  expect(mergeMessageHistory(cached, rows.slice(2, 4), "earlier", rows[4].id))
    .toEqual(rows);
  expect(mergeMessageHistory(cached, rows.slice(1, 4), "earlier", rows[4].id))
    .toEqual(rows);
});
