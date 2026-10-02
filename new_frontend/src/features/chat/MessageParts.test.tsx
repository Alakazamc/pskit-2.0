import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { MessageParts } from "./MessageParts";

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
