import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "../../app/App";

const json = (value: unknown) => new Response(JSON.stringify(value), { status: 200 });
afterEach(() => {
  cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); window.localStorage.clear();
  window.history.replaceState(null, "", "/");
});

it("opens a tool in the full tools area with a back button and tool-scoped history", async () => {
  window.localStorage.setItem("research_access_token", "demo-token");
  window.localStorage.setItem("research_language", "en");
  window.history.replaceState(null, "", "/tools");
  const actor = userEvent.setup();
  const history: string[] = [];
  const invocations: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://test");
    if (url.pathname.endsWith("/me")) return json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (url.pathname.endsWith("/mcp/tools")) return json([
      { name: "fetch_uniprot", description: "Read a protein record", input_schema: {
        properties: { accession: { type: "string" } }, required: ["accession"],
      } },
      { name: "search_pdb", description: "Search protein structures", input_schema: {} },
    ]);
    if (url.pathname.endsWith("/tool-runs")) {
      const tool = url.searchParams.get("tool") ?? "all";
      history.push(tool);
      return json([{ id: tool, tool, title: `${tool} saved call`, arguments: {}, result: {},
        project_id: null, created_at: "2026-10-05T00:00:00Z" }]);
    }
    if (url.pathname.endsWith("/fetch_uniprot/invoke")) {
      invocations.push(JSON.parse(String(init?.body)));
      return json({ tool: "fetch_uniprot", status: "completed", result: { accession: "P12345" } });
    }
    return json([]);
  }));
  render(<App />);
  expect(screen.queryByRole("link", { name: "My runs" })).not.toBeInTheDocument();
  const trigger = await screen.findByRole("link", { name: /fetch_uniprot/ });
  expect(trigger).toHaveClass("catalog-entry-card-large");
  await actor.click(trigger);
  const panel = await screen.findByRole("article", { name: "fetch_uniprot" });
  const header = document.querySelector<HTMLElement>(".mono-topbar")!;
  expect(within(header).getByRole("heading", { name: "fetch_uniprot", level: 1 })).toBeInTheDocument();
  expect(within(header).getByText("Read a protein record")).toBeInTheDocument();
  expect(screen.getAllByText("Read a protein record")).toHaveLength(1);
  expect(within(header).getByRole("button", { name: "Switch to light mode" })).toBeInTheDocument();
  expect(within(panel).queryByRole("heading", { name: "fetch_uniprot" })).not.toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.queryByRole("searchbox", { name: "Search tools" })).not.toBeInTheDocument();
  expect(history).toEqual([]);
  await actor.type(within(panel).getByRole("textbox", { name: "accession" }), "P12345");
  await actor.click(within(panel).getByRole("button", { name: "Run tool" }));
  expect(await within(panel).findByText(/"accession": "P12345"/)).toBeInTheDocument();
  expect(invocations).toEqual([{ accession: "P12345" }]);
  await actor.click(within(panel).getByRole("tab", { name: "Run history" }));
  expect(await within(panel).findByText("fetch_uniprot saved call")).toBeInTheDocument();
  expect(history).toEqual(["fetch_uniprot"]);
  await actor.click(within(header).getByRole("button", { name: "Edit" }));
  const field = within(panel).getByRole("textbox", { name: "accession" });
  expect(field).toHaveValue("P12345");
  // The pencil switches back to parameters and focuses the editable field.
  await vi.waitFor(() => expect(field).toHaveFocus());
  await actor.click(within(header).getByRole("button", { name: "Back to tools" }));
  expect(await screen.findByRole("searchbox", { name: "Search tools" })).toBeInTheDocument();
  expect(within(header).getByRole("heading", { name: "Tools", level: 1 })).toBeInTheDocument();
  expect(within(header).queryByRole("button", { name: "Back to tools" })).not.toBeInTheDocument();
  expect(within(header).queryByText("Read a protein record")).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: /fetch_uniprot/ })).toHaveFocus();
  await actor.click(screen.getByRole("link", { name: /search_pdb/ }));
  const pdb = await screen.findByRole("article", { name: "search_pdb" });
  await actor.click(within(pdb).getByRole("tab", { name: "Run history" }));
  expect(await within(pdb).findByText("search_pdb saved call")).toBeInTheDocument();
  expect(within(pdb).queryByText("fetch_uniprot saved call")).not.toBeInTheDocument();
  expect(history).toEqual(["fetch_uniprot", "search_pdb"]);
}, 15_000);

it.each([
  { language: "en", theme: "light", title: "Structure viewer", back: "Back to tools", description: "Explore PDB and mmCIF structures in 3D." },
  { language: "zh", theme: "dark", title: "结构查看器", back: "返回工具集", description: "查看 PDB 与 mmCIF 三维结构。" },
])("uses the structure viewer's title in the workspace header ($language, $theme)", async ({ language, theme, title, back, description }) => {
  window.localStorage.setItem("research_access_token", "demo-token");
  window.localStorage.setItem("research_language", language);
  window.localStorage.setItem("pskit-theme", theme);
  window.history.replaceState(null, "", "/tools/structure");
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input).endsWith("/me")
    ? json({ id: "alice", name: "Alice", email: "alice@example.org" }) : json([])));
  render(<App />);
  const heading = await screen.findByRole("heading", { name: title, level: 1 });
  const header = document.querySelector<HTMLElement>(".mono-topbar")!;
  expect(header).toContainElement(heading);
  expect(within(header).getByText(description)).toBeInTheDocument();
  expect(within(header).getByRole("button", { name: back })).toBeInTheDocument();
  expect(screen.getAllByRole("heading", { name: title })).toHaveLength(1);
  expect(screen.getAllByText(description)).toHaveLength(1);
});

it("searches compact skill cards and opens their full details without leaving the directory", async () => {
  window.localStorage.setItem("research_access_token", "demo-token");
  window.localStorage.setItem("research_language", "en");
  window.history.replaceState(null, "", "/skills");
  const actor = userEvent.setup();
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (path.endsWith("/skills")) return json([
      { id: "rna-review", name: "RNA review", description: "Review RNA papers and summarize experimental evidence.", version: 3 },
      { id: "structure", name: "Structure analysis", description: "Inspect molecular structures", version: 1 },
    ]);
    return json([]);
  }));
  render(<App />);
  const search = await screen.findByRole("searchbox", { name: "Search skills" });
  await actor.type(search, "experimental");
  expect(screen.queryByRole("button", { name: "Structure analysis" })).not.toBeInTheDocument();
  const card = screen.getByRole("button", { name: "RNA review" });
  expect(card).toHaveClass("catalog-entry-card");
  await actor.click(card);
  const panel = screen.getByRole("dialog", { name: "RNA review" });
  expect(within(panel).getByText("rna-review")).toBeInTheDocument();
  expect(within(panel).getByText("3")).toBeInTheDocument();
  expect(within(panel).getByText("Review RNA papers and summarize experimental evidence.")).toBeInTheDocument();
  await actor.click(within(panel).getByRole("button", { name: "Close details" }));
  expect(card).toHaveFocus();
  expect(search).toHaveValue("experimental");
  await actor.clear(search);
  await actor.type(search, "missing");
  expect(screen.getByText("No matching items")).toBeInTheDocument();
}, 15_000);

it("opens a conversation artifact from its header and keeps preview and download in the detail panel", async () => {
  window.localStorage.setItem("research_access_token", "demo-token");
  window.localStorage.setItem("research_language", "en");
  window.history.replaceState(null, "", "/session/session-1");
  const actor = userEvent.setup();
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/me")) return json({ id: "alice", name: "Alice", email: "alice@example.org" });
    if (path.endsWith("/g")) return json([{ id: "project-alice", name: "Personal", description: "" }]);
    if (path.endsWith("/c")) return json([{ id: "session-1", project_id: "project-alice", title: "Research" }]);
    if (path.endsWith("/sessions/session-1/artifacts")) return json([{ id: "result", name: "report.md", kind: "text", available: true }]);
    if (path.endsWith("/result/preview")) return json({ id: "result", text: "# Analysis result", truncated: false });
    return json([]);
  }));
  render(<App />);
  const trigger = await screen.findByRole("button", { name: "Artifacts" });
  await actor.click(trigger);
  const card = await screen.findByRole("button", { name: /report.md/ });
  expect(screen.queryByRole("button", { name: "Download report.md" })).not.toBeInTheDocument();
  await actor.click(card);
  const panel = await screen.findByRole("dialog", { name: "report.md" });
  expect(await within(panel).findByText("# Analysis result")).toBeInTheDocument();
  expect(within(panel).getByRole("button", { name: "Download report.md" })).toBeEnabled();
  await actor.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
}, 15_000);
