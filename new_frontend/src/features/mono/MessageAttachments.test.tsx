import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { App } from "../../app/App";

const nativeURL = URL;
const createUrl = vi.fn(() => `blob:message-${createUrl.mock.calls.length}`);
const revokeUrl = vi.fn();
const image = new Blob(["image fixture"], { type: "image/png" });
const json = (value: unknown, status = 200) => Response.json(value, { status });

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

function api(download: (id: string) => Promise<Response> = async () => new Response(image)) {
  const downloads: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = new nativeURL(String(input), "http://test").pathname;
    if (path.endsWith("/me")) return json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (path.endsWith("/c")) return json(["first", "second"].map((id) => ({
      id, project_id: "project-alice", title: `${id} chat`, status: "completed",
    })));
    if (path.endsWith("/models")) return json([{ id: "vision-model", supports_images: true }]);
    if (path.endsWith("/messages")) {
      const id = path.includes("/second/") ? "second" : "first";
      return json([{ id: `message-${id}`, session_id: id, role: "user",
        created_at: "2026-10-07T06:18:00Z", parts: [
          { type: "text", text: `Describe ${id}` },
          { type: "file", id: `image-${id}-1`, name: "image.png" },
          { type: "file", id: `image-${id}-2`, name: "image.png" },
          { type: "file", id: `notes-${id}`, name: "notes.txt" },
        ],
      }]);
    }
    if (path.endsWith("/download")) {
      const id = decodeURIComponent(path.split("/").at(-2)!);
      downloads.push(id);
      return download(id);
    }
    return json([]);
  }));
  return downloads;
}

it("loads saved same-name images above a separate user text bubble through owned file IDs", async () => {
  const downloads = api();
  const view = render(<App />);
  await waitFor(() => expect(screen.getAllByRole("img", { name: "image.png" })).toHaveLength(2));
  const pictures = screen.getAllByRole("img", { name: "image.png" });
  expect(new Set(pictures.map((picture) => picture.getAttribute("src"))).size).toBe(2);
  const bubble = screen.getByText("Describe first").closest(".message-body")!;
  expect(bubble).not.toContainElement(pictures[0]);
  expect(bubble).toContainElement(screen.getByText("notes.txt"));
  expect(pictures[0].compareDocumentPosition(bubble) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(downloads.sort()).toEqual(["image-first-1", "image-first-2"]);
  const urls = pictures.map((picture) => picture.getAttribute("src"));
  view.unmount();
  for (const url of urls) expect(revokeUrl).toHaveBeenCalledWith(url);
  expect(Object.values(localStorage).join(" ")).not.toContain("blob:");
});

it("shows pending image previews while retaining the message text", async () => {
  let finish!: (value: Response) => void;
  const pending = new Promise<Response>((resolve) => { finish = resolve; });
  api(async () => (await pending).clone());
  render(<App />);
  await screen.findByText("Describe first");
  expect(await screen.findAllByRole("status", { name: "Loading image image.png" })).toHaveLength(2);
  expect(screen.queryByRole("img", { name: "image.png" })).not.toBeInTheDocument();
  await act(async () => { finish(new Response(image)); });
  await waitFor(() => expect(screen.getAllByRole("img", { name: "image.png" })).toHaveLength(2));
});

it("keeps a named retryable fallback when an owned image cannot be downloaded", async () => {
  let unavailable = true;
  api(async () => unavailable ? json({ detail: "Unavailable" }, 503) : new Response(image));
  render(<App />);
  await waitFor(() => expect(screen.getAllByText("Image unavailable")).toHaveLength(2));
  expect(screen.getByText("Describe first")).toBeVisible();
  unavailable = false;
  await userEvent.setup().click(screen.getAllByRole("button", { name: "Retry image image.png" })[0]);
  expect(await screen.findByRole("img", { name: "image.png" })).toBeVisible();
});

it("isolates late image downloads when switching conversations", async () => {
  let finish!: (value: Response) => void;
  const pending = new Promise<Response>((resolve) => { finish = resolve; });
  const downloads = api(async (id) => id.includes("first") ? (await pending).clone() : new Response(image));
  render(<App />);
  await waitFor(() => expect(downloads).toContain("image-first-1"));
  await userEvent.setup().click(screen.getByRole("link", { name: "second chat" }));
  await screen.findByText("Describe second");
  await waitFor(() => expect(screen.getAllByRole("img", { name: "image.png" })).toHaveLength(2));
  const secondUrls = screen.getAllByRole("img", { name: "image.png" }).map((picture) => picture.getAttribute("src"));
  await act(async () => { finish(new Response(image)); });
  expect(screen.getAllByRole("img", { name: "image.png" }).map((picture) => picture.getAttribute("src"))).toEqual(secondUrls);
  expect(screen.queryByText("Describe first")).not.toBeInTheDocument();
  await userEvent.setup().click(screen.getByRole("link", { name: "first chat" }));
  await screen.findByText("Describe first");
  await waitFor(() => expect(screen.getAllByRole("img", { name: "image.png" })).toHaveLength(2));
  expect(downloads.filter((id) => id.includes("first"))).toHaveLength(2);
  for (const url of secondUrls) expect(revokeUrl).toHaveBeenCalledWith(url);
});
