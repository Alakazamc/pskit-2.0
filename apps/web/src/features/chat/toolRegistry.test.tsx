import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ToolCallPart, registerToolRenderer } from "./toolRegistry";

describe("tool card registry", () => {
  it("uses a registered card and falls back to the generic card", () => {
    registerToolRenderer("search_pdb", ({ part }) => <div>Custom {part.tool}</div>);
    render(<><ToolCallPart part={{ type: "tool_call", tool: "search_pdb", status: "completed", summary: "Found 1A9N" }} /><ToolCallPart part={{ type: "tool_call", tool: "unknown", status: "completed", summary: "Done" }} /></>);
    expect(screen.getByText("Custom search_pdb")).toBeInTheDocument();
    expect(screen.getByText("unknown")).toBeInTheDocument();
  });
});
