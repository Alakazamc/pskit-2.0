import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { MessageParts } from "./MessageParts";

afterEach(cleanup);

it("renders persisted file, citation, tool result, and error parts", () => {
  render(<MessageParts parts={[
    { type: "file", id: "file-1", name: "paper.pdf" },
    { type: "citation", title: "PDB record", url: "https://www.rcsb.org/structure/1A9N" },
    { type: "tool_result", tool: "search_pdb", result: { hits: 2 } },
    { type: "error", code: "TOOL_FAILED", message: "Search failed" },
  ]} />);
  expect(screen.getByText("paper.pdf")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "PDB record" })).toHaveAttribute("href", "https://www.rcsb.org/structure/1A9N");
  expect(screen.getByText(/search_pdb/)).toBeInTheDocument();
  expect(screen.getByText("Search failed")).toBeInTheDocument();
});

it("shows progress parts using their reported value", () => {
  render(<MessageParts parts={[{ type: "progress", label: "正在计算", value: 42 }]} />);
  expect(screen.getByRole("progressbar", { name: "正在计算" })).toHaveAttribute("aria-valuenow", "42");
  expect(screen.queryByText(/42%/)).not.toBeInTheDocument();
});
