import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { App } from "../../app/App";
import { emptyComposerDraft, readComposerDraft, writeComposerDraft } from "../chat/composerDrafts";

const scope = { userId: "alice", conversationId: "session:first" };
const image = new Blob(["image fixture"], { type: "image/png" });
const json = (value: unknown, status = 200) => Response.json(value, { status });
const nativeURL = URL;
const createUrl = vi.fn(() => `blob:preview-${createUrl.mock.calls.length}`);
const revokeUrl = vi.fn();
const thumbnails = () => document.querySelectorAll<HTMLImageElement>(".attachment-preview img");

beforeEach(() => {
  localStorage.setItem("research_access_token", "demo-token");
  localStorage.setItem("research_language", "en");
  window.history.replaceState(null, "", "/session/first");
  vi.stubGlobal("URL", class extends nativeURL {
    static createObjectURL = createUrl;
    static revokeObjectURL = revokeUrl;
  });
});
afterEach(() => {
  cleanup(); localStorage.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks();
  createUrl.mockClear(); revokeUrl.mockClear();
  window.history.replaceState(null, "", "/");
});

function api(download: () => Promise<Response> = async () => new Response(image)) {
  let uploads = 0;
  const requests: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new nativeURL(String(input), "http://test").pathname;
    requests.push(path);
    if (path.endsWith("/me")) return json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (path.endsWith("/c")) return json(["first", "second"].map((id) => ({ id, project_id: "project-alice", title: `${id} chat`, status: "completed" })));
    if (path.endsWith("/models")) return json([{ id: "vision-model", supports_images: true }]);
    if (path.endsWith("/files/content") && init?.method === "PUT") return json({ id: `image-${++uploads}`, name: "image.png" });
    if (path.endsWith("/download")) return download();
    return json([]);
  }));
  return requests;
}

it("restores uploaded image previews after switching away and back, including identical filenames", async () => {
  const requests = api();
  const actor = userEvent.setup();
  const { container } = render(<App />);
  await screen.findByRole("textbox", { name: "Message" });
  await screen.findByRole("button", { name: /Choose model and thinking level: vision-model/ });
  await actor.upload(container.querySelector('input[type="file"]')!, [
    new File(["first image"], "image.png", { type: "image/png" }),
    new File(["second image"], "image.png", { type: "image/png" }),
  ]);
  await waitFor(() => expect(screen.getAllByRole("group", { name: "Uploaded image.png" })).toHaveLength(2));
  expect(thumbnails()).toHaveLength(2);
  await actor.click(screen.getByRole("link", { name: "second chat" }));
  await screen.findByRole("textbox", { name: "Message" });
  expect(thumbnails()).toHaveLength(0);
  await actor.click(screen.getByRole("link", { name: "first chat" }));
  await waitFor(() => expect(thumbnails()).toHaveLength(2));
  expect(new Set(Array.from(thumbnails(), (img) => img.src)).size).toBe(2);
  expect(requests.filter((path) => path.endsWith("/download"))).toHaveLength(0);
  expect(readComposerDraft(scope).attachments).toEqual([{ id: "image-1", name: "image.png" }, { id: "image-2", name: "image.png" }]);
  expect(Object.values(localStorage).join(" ")).not.toContain("blob:");
  await actor.click(screen.getAllByRole("button", { name: "Remove image.png" })[0]);
  expect(thumbnails()).toHaveLength(1);
  expect(readComposerDraft(scope).attachments).toEqual([{ id: "image-2", name: "image.png" }]);
});

it("rebuilds a persisted image thumbnail from the owned file API after a refresh", async () => {
  writeComposerDraft(scope, { ...emptyComposerDraft(), attachments: [{ id: "image-1", name: "image.png" }] });
  const requests = api();
  const view = render(<App />);
  await waitFor(() => expect(thumbnails()).toHaveLength(1));
  expect(requests.filter((path) => path.endsWith("/download"))).toEqual(["/api/v1/files/image-1/download"]);
  const url = thumbnails()[0].src;
  view.unmount();
  expect(revokeUrl).toHaveBeenCalledWith(url);
  expect(readComposerDraft(scope).attachments).toHaveLength(1);
});

it("does not bring an attachment back when its thumbnail arrives after removal", async () => {
  writeComposerDraft(scope, { ...emptyComposerDraft(), attachments: [{ id: "image-1", name: "image.png" }] });
  let finish!: (response: Response) => void;
  const pending = new Promise<Response>((resolve) => { finish = resolve; });
  const requests = api(() => pending);
  render(<App />);
  await waitFor(() => expect(requests).toContain("/api/v1/files/image-1/download"));
  await userEvent.setup().click(await screen.findByRole("button", { name: "Remove image.png" }));
  await act(async () => { finish(new Response(image)); });
  expect(thumbnails()).toHaveLength(0);
  expect(screen.queryByRole("group", { name: "Uploaded image.png" })).not.toBeInTheDocument();
  expect(readComposerDraft(scope).attachments).toEqual([]);
});

it("keeps the file reference usable if its thumbnail cannot be loaded", async () => {
  writeComposerDraft(scope, { ...emptyComposerDraft(), attachments: [{ id: "image-1", name: "image.png" }] });
  const requests = api(async () => json({ detail: "Unavailable" }, 503));
  render(<App />);
  await waitFor(() => expect(requests).toContain("/api/v1/files/image-1/download"));
  expect(await screen.findByRole("button", { name: "Remove image.png" })).toBeEnabled();
  expect(thumbnails()).toHaveLength(0);
  expect(readComposerDraft(scope).attachments).toEqual([{ id: "image-1", name: "image.png" }]);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});
