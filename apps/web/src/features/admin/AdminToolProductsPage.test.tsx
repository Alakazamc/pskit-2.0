import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "../../app/App";
import type { QualificationReport } from "../../api/generated/types.gen";
import { administrator, json, serviceRow, setupAdmin } from "./adminTestUtils";

const discovery = {
  discovery_id: "discovery-1", service_id: "rna", service_revision: 1,
  protocol: { transport: "streamable_http", paginated: true }, digest: "d".repeat(64), discovered_at: "2026-10-06T00:00:00Z",
  tools: [{ name: "rna_generate", description: "Generate RNA candidates", input_schema: { type: "object", properties: { protein: { type: "string" } }, required: ["protein"] }, remote_output_schema: { type: "object", properties: { candidates: { type: "array" } }, required: ["candidates"] } }],
};

const report: QualificationReport = {
  report_id: "report-1", product_id: "product-rna-generate", product_revision: 1, service_revision: 1,
  binding_digest: "b".repeat(64), ui_digest: "u".repeat(64), suite_digest: "s".repeat(64), status: "passed",
  protocol_passed: true, scientific_passed: true, qualified_at: "2026-10-06T00:01:00Z",
  cases: [{ case_id: "smoke", status: "passed", protocol_assertions: { usage_complete: true, idempotent: true }, scientific_assertions: { result_shape: true }, usage_source: "service_reported", message: "service_reported GPU usage" }],
};

afterEach(() => { cleanup(); vi.unstubAllGlobals(); localStorage.clear(); window.history.replaceState(null, "", "/"); });

it("connects, discovers, maps, previews, qualifies and publishes without adding product code", async () => {
  let savedDraft: Record<string, unknown> | null = null;
  setupAdmin("/admin/tool-products", (path, method, body) => {
    if (path.endsWith("/services") && method === "GET") return json({ items: [serviceRow], next_cursor: null });
    if (path.endsWith("/mcp-probes")) {
      expect(body).toEqual(expect.objectContaining({ service_id: "rna", uri: "https://lab.example/mcp", credential_ref: "service/rna" }));
      return json({ probe_id: "probe-1", endpoint_id: "endpoint-1", service_id: "rna", service_revision: 1, uri: "https://lab.example/mcp", transport: "streamable_http", credential_ref: "service/rna", network_zone: "public", protocol: { ok: true }, addresses: ["203.0.113.8"], checked_at: "2026-10-06T00:00:00Z" });
    }
    if (path.endsWith("/services/rna/discoveries")) return json(discovery);
    if (path.endsWith("/tool-products/product-rna-generate/draft") && method === "PUT") {
      savedDraft = body.draft as Record<string, unknown>;
      expect(savedDraft).toEqual(expect.objectContaining({ slug: "rna-generate", owner_user_id: "alice" }));
      return json(savedDraft);
    }
    if (path.endsWith("/tool-products/product-rna-generate/qualifications")) return json(report);
    if (path.endsWith("/tool-product-releases/report-1/publish")) return json({ release_id: "release-1", product_id: "product-rna-generate", slug: "rna-generate", revision: 1, title: { en: "RNA generate", "zh-CN": "RNA generate" }, description: { en: "Generate RNA candidates", "zh-CN": "Generate RNA candidates" }, ui_schema: (savedDraft as { ui_schema: unknown }).ui_schema, actions: (savedDraft as { actions: unknown }).actions, state: "published", published_at: "2026-10-06T00:02:00Z" });
  }, administrator, "en");
  render(<App />);
  const actor = userEvent.setup();
  expect(await screen.findByRole("heading", { name: "Tool products" })).toBeInTheDocument();
  await actor.type(screen.getByLabelText("MCP service URL"), "https://lab.example/mcp");
  await actor.type(screen.getByLabelText("Service ID"), "rna");
  await actor.type(screen.getByLabelText("Credential reference"), "service/rna");
  await actor.click(screen.getByRole("button", { name: "Probe endpoint" }));
  expect(await screen.findByText(/endpoint-1/)).toBeInTheDocument();
  await actor.selectOptions(screen.getByLabelText("Approved service"), "rna");
  await actor.type(screen.getByLabelText("Change reason"), "Discover reviewed MCP tools");
  await actor.click(screen.getByRole("button", { name: "Discover capabilities" }));
  await actor.click(await screen.findByRole("button", { name: "Use rna_generate" }));
  expect(screen.getByRole("heading", { name: "RNA generate" })).toBeInTheDocument();
  expect(screen.getByLabelText("protein")).toBeInTheDocument();
  await actor.click(screen.getByRole("button", { name: "Save product draft" }));
  await actor.click(await screen.findByRole("button", { name: "Run qualification" }));
  expect(await screen.findByRole("heading", { name: "Protocol evidence" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Scientific evidence" })).toBeInTheDocument();
  expect(screen.getByText(/service_reported GPU usage/)).toBeInTheDocument();
  await actor.click(screen.getByRole("button", { name: "Review release" }));
  const review = screen.getByRole("region", { name: "Release diff" });
  expect(within(review).getByText(/Revision 1/)).toBeInTheDocument();
  await actor.click(within(review).getByRole("button", { name: "Publish product" }));
  expect(await screen.findByText("Published release release-1")).toBeInTheDocument();
}, 15_000);

it("lets an auditor load separated evidence but exposes no edit or publish controls", async () => {
  setupAdmin("/admin/tool-products", (path) => {
    if (path.endsWith("/qualifications/report-1")) return json(report);
    if (path.endsWith("/services")) return json({ items: [], next_cursor: null });
  }, { user_id: "auditor", roles: ["auditor"], permissions: ["services:read"], service_ids: ["rna"] }, "en");
  render(<App />);
  const actor = userEvent.setup();
  await actor.type(await screen.findByLabelText("Qualification report ID"), "report-1");
  await actor.click(screen.getByRole("button", { name: "Load evidence" }));
  expect(await screen.findByRole("heading", { name: "Protocol evidence" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Scientific evidence" })).toBeInTheDocument();
  expect(screen.queryByLabelText("MCP service URL")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Publish product" })).not.toBeInTheDocument();
});

it("invalidates qualification approval when the builder changes", async () => {
  setupAdmin("/admin/tool-products", (path, method, body) => {
    if (path.endsWith("/services")) return json({ items: [serviceRow], next_cursor: null });
    if (path.endsWith("/services/rna/discoveries")) return json(discovery);
    if (path.includes("/draft") && method === "PUT") return json(body.draft);
    if (path.includes("/qualifications") && method === "POST") return json(report);
  }, administrator, "en");
  render(<App />);
  const actor = userEvent.setup();
  await screen.findByRole("option", { name: "RNA" });
  await actor.selectOptions(await screen.findByLabelText("Approved service"), "rna");
  await actor.type(screen.getByLabelText("Change reason"), "Review current tool schema");
  await actor.click(screen.getByRole("button", { name: "Discover capabilities" }));
  await actor.click(await screen.findByRole("button", { name: "Use rna_generate" }));
  await actor.click(screen.getByRole("button", { name: "Save product draft" }));
  await actor.click(await screen.findByRole("button", { name: "Run qualification" }));
  await actor.clear(screen.getByLabelText("English title"));
  await actor.type(screen.getByLabelText("English title"), "Changed product");
  expect(screen.getByRole("alert")).toHaveTextContent("Qualification is stale");
  expect(screen.queryByRole("button", { name: "Review release" })).not.toBeInTheDocument();
});

it("lets a maintainer configure and qualify only an assigned service without publication controls", async () => {
  setupAdmin("/admin/tool-products", (path, method, body) => {
    if (path.endsWith("/services")) return json({ items: [serviceRow], next_cursor: null });
    if (path.endsWith("/services/rna/discoveries")) return json(discovery);
    if (path.includes("/draft") && method === "PUT") return json(body.draft);
    if (path.includes("/qualifications") && method === "POST") return json(report);
  }, { user_id: "alice", roles: ["service_maintainer"], permissions: ["services:read", "services:write"], service_ids: ["rna"] }, "en");
  render(<App />);
  const actor = userEvent.setup();
  await screen.findByRole("option", { name: "RNA" });
  expect(screen.queryByLabelText("MCP service URL")).not.toBeInTheDocument();
  await actor.selectOptions(screen.getByLabelText("Approved service"), "rna");
  await actor.type(screen.getByLabelText("Change reason"), "Qualify assigned research tool");
  await actor.click(screen.getByRole("button", { name: "Discover capabilities" }));
  await actor.click(await screen.findByRole("button", { name: "Use rna_generate" }));
  await actor.click(screen.getByRole("button", { name: "Save product draft" }));
  await actor.click(await screen.findByRole("button", { name: "Run qualification" }));
  expect(await screen.findByRole("heading", { name: "Protocol evidence" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Review release" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Publish product" })).not.toBeInTheDocument();
});

it("keeps a failed qualification visible and blocks publisher approval", async () => {
  const failed: QualificationReport = { ...report, status: "failed", protocol_passed: false, scientific_passed: false, cases: [{ ...report.cases[0], status: "failed", protocol_assertions: { usage_complete: false }, scientific_assertions: { result_shape: false }, usage_source: "unknown", message: "Missing GPU timing" }] };
  setupAdmin("/admin/tool-products", (path) => {
    if (path.endsWith("/qualifications/report-failed")) return json(failed);
    if (path.endsWith("/services")) return json({ items: [serviceRow], next_cursor: null });
  }, administrator, "en");
  render(<App />);
  const actor = userEvent.setup();
  await actor.type(await screen.findByLabelText("Qualification report ID"), "report-failed");
  await actor.click(screen.getByRole("button", { name: "Load evidence" }));
  expect(await screen.findByText(/Missing GPU timing/)).toBeInTheDocument();
  expect(screen.getByText("A failed case blocks publication of this revision.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Review release" })).not.toBeInTheDocument();
}, 7_000);
