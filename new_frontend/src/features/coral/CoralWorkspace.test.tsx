import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "../../app/App";

afterEach(() => {
  cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); localStorage.clear();
  window.history.replaceState(null, "", "/");
});

it("runs CORAL with server progress, actual results and input-bound analysis in a new chat", async () => {
  localStorage.setItem("research_access_token", "demo-token");
  localStorage.setItem("research_language", "en");
  window.history.replaceState(null, "", "/tools");
  const json = (value: unknown) => new Response(JSON.stringify(value), { status: 200 });
  let status = "queued";
  let submitted: Record<string, unknown> | undefined;
  let message: Record<string, unknown> | undefined;
  const uploads: string[] = [];
  const capability = {
    id: "coral.generate_rna", version: "1", visibility: "published", gpu_count: 1, required_usage: ["gpu_device_ms"],
    input_schema: { type: "object", properties: {
      pdb_id: { type: "string" }, chain: { type: "string" },
      length: { type: "integer", minimum: 1, maximum: 200 },
      num_samples: { type: "integer", minimum: 1, maximum: 10000 },
    }, required: ["pdb_id", "chain", "length", "num_samples"] },
    max_budget: { cpu_core_ms: 0, gpu_device_ms: 60000 },
  };
  const job = () => ({
    id: "job-coral", user_id: "alice", service_id: "coral", capability,
    arguments: (submitted?.arguments ?? {}), budget: capability.max_budget, status,
    progress: status === "running" ? 42 : status === "completed" ? 100 : 0,
    accounting_status: status === "completed" ? "settled" : "reserved",
    report: status === "completed" ? { status: "completed", result: {
      candidates: [{ id: "rna-1", sequence: "ACGU" }, { id: "rna-2", sequence: "UUAG" }, { id: "rna-3", sequence: "GCAU" }],
    }, artifacts: [], usage: { source: "service_reported", gpu_device_ms: 5000 } } : null,
  });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://test");
    const path = url.pathname;
    if (path.endsWith("/me")) return json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (path.endsWith("/compute/capabilities")) return json([capability]);
    if (path.endsWith("/compute/jobs") && init?.method === "POST") {
      expect(new Headers(init.headers).get("Idempotency-Key")).toBeTruthy();
      submitted = JSON.parse(String(init.body)); return json(job());
    }
    if (path.endsWith("/compute/jobs/job-coral")) return json(job());
    if (path.endsWith("/files/content") && init?.method === "PUT") {
      const name = url.searchParams.get("name")!;
      uploads.push(name);
      return json({ id: "dataset-file", name, status: "ready", size: (init.body as Blob).size });
    }
    if (path === "/api/v1/c" && init?.method === "POST") return json({ id: "session-analysis", project_id: "project-alice", title: "CORAL analysis" });
    if (path.endsWith("/c/session-analysis/messages") && init?.method === "POST") {
      message = JSON.parse(String(init.body)); return json({ run_id: "run-analysis" });
    }
    return json([]);
  }));
  render(<App />); const actor = userEvent.setup();
  await actor.click(await screen.findByRole("link", { name: /CORAL/ }));
  const panel = await screen.findByRole("dialog", { name: "CORAL" });
  await actor.type(within(panel).getByRole("textbox", { name: "PDB ID" }), "1a9n");
  expect(within(panel).getByRole("spinbutton", { name: "Sequence count" })).toHaveValue(10000);
  expect(within(panel).getByRole("spinbutton", { name: "RNA length" })).toHaveValue(100);
  await actor.click(within(panel).getByRole("button", { name: "Generate RNA" }));
  await waitFor(() => expect(submitted).toMatchObject({
    capability_id: "coral.generate_rna", version: "1",
    arguments: { pdb_id: "1A9N", chain: "A", num_samples: 10000, length: 100 },
  }));
  expect(await within(panel).findByText("Queued", {}, { timeout: 5000 })).toBeInTheDocument();
  expect(within(panel).queryByText("3 sequences")).not.toBeInTheDocument();
  status = "running";
  expect(await within(panel).findByText("42%", {}, { timeout: 5000 })).toBeInTheDocument();
  status = "completed";
  expect(await within(panel).findByText("3 sequences", {}, { timeout: 5000 })).toBeInTheDocument();
  expect(within(panel).getByText("4 nt")).toBeInTheDocument();
  // Later form edits must not change the context belonging to the completed job.
  const pdb = within(panel).getByRole("textbox", { name: "PDB ID" });
  await actor.clear(pdb); await actor.type(pdb, "2XYZ");
  await actor.click(within(panel).getByRole("button", { name: "Ask Agent" }));
  await actor.click(screen.getByRole("menuitem", { name: "Analyze sequences" }));
  await waitFor(() => expect(window.location.pathname).toBe("/session/session-analysis"), { timeout: 5000 });
  expect(uploads).toHaveLength(1);
  expect(message?.attachments).toEqual([{ id: "dataset-file", name: "coral-job-coral-1.fasta" }]);
  expect(String(message?.content)).toContain("1A9N");
  expect(String(message?.content)).not.toContain("2XYZ");
  expect(String(message?.content)).toContain("job-coral");
  expect(String(message?.content)).toContain("composition");
}, 20_000);

it("opens only CORAL history and restores the owned job without submitting again", async () => {
  localStorage.setItem("research_access_token", "demo-token");
  localStorage.setItem("research_language", "en");
  window.history.replaceState(null, "", "/tools/coral?tab=history");
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://test");
    let data: unknown = [];
    if (url.pathname.endsWith("/me")) data = { id: "alice", name: "Alice" };
    if (url.pathname.endsWith("/compute/jobs")) {
      expect(url.searchParams.get("capability_id")).toBe("coral.generate_rna");
      data = [{ id: "old-coral", capability_id: "coral.generate_rna", version: "1", arguments: { pdb_id: "1A9N", chain: "A" }, status: "completed", progress: 100, created_at: "2026-10-05T10:00:00Z" }];
    }
    if (url.pathname.endsWith("/compute/jobs/old-coral")) data = {
      id: "old-coral", user_id: "alice", service_id: "coral", capability: { id: "coral.generate_rna", version: "1" }, arguments: { pdb_id: "1A9N", chain: "A" }, status: "completed",
      report: { status: "completed", result: { sequences: ["ACGU"] }, artifacts: [], usage: { source: "unknown" } },
    };
    return new Response(JSON.stringify(data), { status: 200 });
  });
  vi.stubGlobal("fetch", fetcher);
  render(<App />);
  const actor = userEvent.setup();
  await actor.click(await screen.findByRole("button", { name: /1A9N/ }));
  expect(await screen.findByText("1 sequences", {}, { timeout: 5000 })).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "PDB ID" })).toHaveValue("1A9N");
  expect(window.location.search).toBe("?job=old-coral");
  expect(fetcher.mock.calls.some(([input]) => String(input).includes("/mcp/runs"))).toBe(false);
}, 15_000);
