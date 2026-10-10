import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { LanguageProvider } from "../../i18n/LanguageProvider";
import { StructureViewerPage } from "./StructureViewerPage";

vi.mock("./MolstarCanvas", () => ({
  default: ({ source }: { source: { type: "pdb" | "file"; id?: string; file?: File } }) =>
    <div data-testid="molecular-canvas">{source.type === "pdb" ? source.id : source.file?.name}</div>,
}));

afterEach(() => { cleanup(); window.localStorage.clear(); vi.unstubAllGlobals(); });

it("validates PDB IDs before loading and opens a valid structure", async () => {
  render(<LanguageProvider><MemoryRouter initialEntries={["/tools/structure"]}><StructureViewerPage /></MemoryRouter></LanguageProvider>);
  const actor = userEvent.setup();
  const input = screen.getByRole("textbox", { name: "PDB ID" });
  await actor.type(input, "p53");
  await actor.click(screen.getByRole("button", { name: "打开结构" }));
  expect(screen.getByRole("alert")).toHaveTextContent("4 位");
  expect(screen.queryByTestId("molecular-canvas")).not.toBeInTheDocument();
  await actor.clear(input);
  await actor.type(input, "1a9n");
  await actor.click(screen.getByRole("button", { name: "打开结构" }));
  expect(await screen.findByTestId("molecular-canvas")).toHaveTextContent("1A9N");
});

it("accepts local mmCIF files without uploading and rejects unrelated formats", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  render(<LanguageProvider><MemoryRouter initialEntries={["/tools/structure"]}><StructureViewerPage /></MemoryRouter></LanguageProvider>);
  const actor = userEvent.setup({ applyAccept: false });
  const input = screen.getByLabelText("选择 PDB 或 mmCIF 文件") as HTMLInputElement;
  await actor.upload(input, new File(["wrong"], "notes.txt", { type: "text/plain" }));
  expect(screen.getByRole("alert")).toHaveTextContent("PDB 或 mmCIF");
  await actor.upload(input, new File(["data_demo\n"], "demo.cif", { type: "text/plain" }));
  expect(await screen.findByTestId("molecular-canvas")).toHaveTextContent("demo.cif");
  expect(fetcher).not.toHaveBeenCalled();
});
