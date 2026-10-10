import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import type { ResearchApi } from "../../api/types";
import { LanguageProvider } from "../../i18n/LanguageProvider";
import { WorkspacePage } from "./WorkspacePage";

vi.mock("react-resizable-panels", () => ({
  Group: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  Panel: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  Separator: () => <div />,
}));

const api = {
  getProjects: async () => [{ id: "project-a", name: "Lab A", description: "" }],
  getSessions: async () => [],
  getUsage: async () => ({
    tokens: { limit: 100, used: 0, reserved: 0, remaining: 100, unit: "tokens", period: "month", resets_at: "2026-11-01T00:00:00Z" },
    gpu: { limit: 60, used: 0, reserved: 0, remaining: 60, unit: "gpu_minutes", period: "day", resets_at: "2026-10-02T00:00:00Z" },
  }),
  getSkills: async () => [], getResources: async () => [], getFiles: async () => [], getArtifacts: async () => [],
} as unknown as ResearchApi;

function renderSettings() {
  return render(<LanguageProvider><QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter initialEntries={["/settings"]}>
      <WorkspacePage api={api} user={{ id: "alice", name: "Alice", email: "alice@example.org" }} onLogout={() => {}} />
    </MemoryRouter>
  </QueryClientProvider></LanguageProvider>);
}

afterEach(() => {
  cleanup();
  window.localStorage.removeItem("research_language");
  document.documentElement.lang = "";
});

it("switches the workspace to English and restores that choice after remount", async () => {
  window.localStorage.removeItem("research_language");
  const user = userEvent.setup();
  const view = renderSettings();
  expect(screen.getByRole("heading", { name: "工作区设置" })).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "English" }));
  expect(screen.getByRole("heading", { name: "Workspace settings" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Projects" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute("href", "/settings");
  expect(await screen.findByText("Monthly Token quota")).toBeInTheDocument();
  expect(window.localStorage.getItem("research_language")).toBe("en");
  expect(document.documentElement.lang).toBe("en");

  view.unmount();
  renderSettings();
  expect(screen.getByRole("heading", { name: "Workspace settings" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "简体中文" }));
  expect(screen.getByRole("heading", { name: "工作区设置" })).toBeInTheDocument();
  expect(window.localStorage.getItem("research_language")).toBe("zh");
});
