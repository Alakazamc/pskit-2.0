import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "../../app/App";

afterEach(() => {
  cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); localStorage.clear();
  window.history.replaceState(null, "", "/");
});

const capability = { id: "coral.generate_rna", version: "1", gpu_count: 1, required_usage: ["gpu_device_ms"], input_schema: { type: "object", properties: {
  num_samples: { minimum: 1, maximum: 100 }, length: { minimum: 1, maximum: 200 },
} }, max_budget: { cpu_core_ms: 0, gpu_device_ms: 60000 } };
const readyJob = (result: Record<string, unknown>) => ({
  id: "job-ready", user_id: "alice", service_id: "coral", capability, arguments: { pdb_id: "1A9N", chain: "A", num_samples: 10000, length: 100 },
  status: "completed", report: { status: "completed", result, artifacts: [], usage: { source: "service_reported", gpu_device_ms: 5000 } },
});

function setup(path: string, intercept: (url: URL, init?: RequestInit) => unknown | Promise<unknown>) {
  localStorage.setItem("research_access_token", "demo-token"); localStorage.setItem("research_language", "en");
  window.history.replaceState(null, "", path);
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://test");
    const data = url.pathname.endsWith("/me") ? { id: "alice", name: "Alice" } : await intercept(url, init);
    return data instanceof Response ? data : new Response(JSON.stringify(data ?? []), { status: 200 });
  });
  vi.stubGlobal("fetch", fetcher); render(<App />); return { actor: userEvent.setup(), fetcher };
}

it("shows an unavailable service without allowing a fake run", async () => {
  setup("/tools/coral", () => []);
  expect(await screen.findByText(/CORAL is not connected/, {}, { timeout: 5000 })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Generate RNA" })).toBeDisabled();
  expect(screen.queryByRole("button", { name: "Ask Agent" })).not.toBeInTheDocument();
});

it("uses the advertised count limit and rejects an incomplete protein input", async () => {
  const { actor, fetcher } = setup("/tools/coral", (url) => url.pathname.endsWith("/compute/capabilities") ? [capability] : []);
  const count = await screen.findByRole("spinbutton", { name: "Sequence count" }, { timeout: 5000 });
  await waitFor(() => expect(count).toHaveValue(100));
  await actor.type(screen.getByRole("textbox", { name: "PDB ID" }), "1");
  await actor.click(screen.getByRole("button", { name: "Generate RNA" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("four-character PDB ID");
  expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
});

it("cancels a restored queued job through its owned API", async () => {
  let status = "queued";
  const { actor, fetcher } = setup("/tools/coral?job=job-ready", (url, init) => {
    if (url.pathname.endsWith("/compute/capabilities")) return [capability];
    if (url.pathname.endsWith("/cancel") && init?.method === "POST") status = "cancelled";
    if (url.pathname.includes("/compute/jobs/job-ready")) return { ...readyJob({}), report: null, status };
    return [];
  });
  await actor.click(await screen.findByRole("button", { name: "Stop task" }));
  expect(await screen.findByText("Stopped", { exact: true })).toBeInTheDocument();
  expect(fetcher.mock.calls.some(([input, init]) => String(input).endsWith("/compute/jobs/job-ready/cancel") && init?.method === "POST")).toBe(true);
});

it("splits the full dataset and retries uploads and sends without duplicating a chat", async () => {
  const dataset = readyJob({ sequences: Array.from({ length: 10000 }, () => "ACGU".repeat(25)) });
  let attempts = 0, failUpload = true, failSend = true, chats = 0;
  const sizes: number[] = [], sent: { body: Record<string, unknown>; key: string | null }[] = [];
  const { actor } = setup("/tools/coral?job=job-ready", (url, init) => {
    if (url.pathname.endsWith("/compute/capabilities")) return [capability];
    if (url.pathname.endsWith("/compute/jobs/job-ready")) return dataset;
    if (url.pathname.endsWith("/files/content")) {
      attempts += 1;
      if (attempts === 2 && failUpload) return new Response("{}", { status: 429 });
      sizes.push((init?.body as Blob).size);
      return { id: `data-${sizes.length}`, name: url.searchParams.get("name"), status: "ready" };
    }
    if (url.pathname === "/api/v1/c" && init?.method === "POST") { chats += 1; return { id: "analysis", title: "CORAL" }; }
    if (url.pathname.endsWith("/c/analysis/messages") && init?.method === "POST") {
      sent.push({ body: JSON.parse(String(init.body)), key: new Headers(init.headers).get("Idempotency-Key") });
      return failSend ? new Response("{}", { status: 503 }) : { run_id: "run-analysis" };
    }
    return [];
  });
  expect(await screen.findByText("10,000 sequences", {}, { timeout: 5000 })).toBeInTheDocument();
  const choose = async () => {
    await actor.click(screen.getByRole("button", { name: "Ask Agent" }));
    await actor.click(screen.getByRole("menuitem", { name: "Analyze sequences" }));
  };
  await choose(); await screen.findByRole("alert");
  expect(chats).toBe(0); expect(attempts).toBe(2);
  failUpload = false;
  await choose(); await screen.findByRole("alert");
  expect(attempts).toBe(3); expect(sizes).toHaveLength(2); expect(chats).toBe(1);
  failSend = false;
  await choose();
  await waitFor(() => expect(window.location.pathname).toBe("/session/analysis"), { timeout: 5000 });
  expect(attempts).toBe(3); expect(chats).toBe(1); expect(sent).toHaveLength(2);
  expect(sent[0].key).toBeTruthy(); expect(sent[1].key).toBe(sent[0].key);
  expect(sent[1].body.attachments).toHaveLength(2);
  expect(String(sent[1].body.content).length).toBeLessThan(14000);
  expect(Math.max(...sizes)).toBeLessThanOrEqual(1048576);
}, 20_000);

it("reports partial datasets and does not attach a preview as the complete dataset", async () => {
  let message: Record<string, unknown> = {};
  const { actor, fetcher } = setup("/tools/coral?job=job-ready", (url, init) => {
    if (url.pathname.endsWith("/compute/capabilities")) return [capability];
    if (url.pathname.endsWith("/compute/jobs/job-ready")) return readyJob({ sequence_count: 10000, sequence_length: 100, sequences: ["ACGU"] });
    if (url.pathname === "/api/v1/c" && init?.method === "POST") return { id: "partial", title: "CORAL" };
    if (url.pathname.endsWith("/c/partial/messages") && init?.method === "POST") { message = JSON.parse(String(init.body)); return { run_id: "run-partial" }; }
    return [];
  });
  const panel = await screen.findByRole("dialog", { name: "CORAL" });
  expect(await within(panel).findByText(/preview only/, {}, { timeout: 5000 })).toBeInTheDocument();
  expect(within(panel).queryByRole("button", { name: "Download FASTA" })).not.toBeInTheDocument();
  await actor.click(within(panel).getByRole("button", { name: "Ask Agent" }));
  await actor.click(screen.getByRole("menuitem", { name: "Design validation plan" }));
  await waitFor(() => expect(window.location.pathname).toBe("/session/partial"));
  expect(message.attachments).toEqual([]);
  expect(String(message.content)).toContain('"full_dataset_available": false');
  expect(String(message.content)).toContain("negative controls");
  expect(fetcher.mock.calls.some(([input]) => String(input).includes("/files/content"))).toBe(false);
}, 15_000);

it("downloads a large dataset as one FASTA while keeping upload chunks separate", async () => {
  const blobs: Blob[] = [];
  const OriginalURL = URL;
  vi.stubGlobal("URL", class extends OriginalURL {
    static createObjectURL(blob: Blob) { blobs.push(blob); return "blob:coral-test"; }
    static revokeObjectURL() {}
  });
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  const { actor } = setup("/tools/coral?job=job-ready", (url) => {
    if (url.pathname.endsWith("/compute/jobs/job-ready")) return readyJob({ sequences: Array.from({ length: 10000 }, () => "ACGU".repeat(25)) });
    return [];
  });
  await actor.click(await screen.findByRole("button", { name: "Download FASTA" }, { timeout: 5000 }));
  expect(click).toHaveBeenCalledTimes(1);
  expect(blobs).toHaveLength(1);
  expect(blobs[0].size).toBeGreaterThan(1048576);
}, 15_000);

it("does not render another capability's job as a CORAL result", async () => {
  setup("/tools/coral?job=job-ready", (url) => url.pathname.endsWith("/compute/jobs/job-ready")
    ? { ...readyJob({ sequences: ["ACGU"] }), capability: { ...capability, id: "lab.other" } } : []);
  expect(await screen.findByRole("alert", {}, { timeout: 5000 })).toHaveTextContent("not a CORAL task");
  expect(screen.queryByRole("button", { name: "Ask Agent" })).not.toBeInTheDocument();
});
