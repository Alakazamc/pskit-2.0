import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { UsageCard } from "./UsageCard";

describe("usage card", () => {
  it("labels estimated token usage separately from GPU reservations", () => {
    render(<UsageCard usage={{
      tokens: { limit: 1_000_000, used: 0, reserved: 0, remaining: 1_000_000, unit: "tokens", period: "month", resets_at: "2026-11-01T00:00:00Z" },
      gpu: { limit: 60, used: 0, reserved: 20, remaining: 40, unit: "gpu_minutes", period: "day", resets_at: "2026-10-02T00:00:00Z" },
    }} />);
    expect(screen.getByText(/优先按模型回报的 Token 统计/)).toBeTruthy();
    expect(screen.queryByText(/New API 额度/)).not.toBeInTheDocument();
    expect(screen.getByText(/预留 20/)).toBeTruthy();
  });
});
