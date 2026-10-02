import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { LanguageProvider } from "../../i18n/LanguageProvider";
import { ProjectSidebar } from "./ProjectSidebar";

it("creates a project and a session through callbacks", async () => {
  const onCreateProject = vi.fn().mockResolvedValue(undefined);
  const onCreateSession = vi.fn().mockResolvedValue(undefined);
  render(<LanguageProvider><ProjectSidebar
    projects={[{ id: "project-1", name: "Lab", description: "" }]}
    sessions={[]} files={[]} selectedProject="project-1" selected={null}
    user={{ id: "alice", name: "Alice", email: "alice@example.org" }}
    onSelectProject={() => {}} onSelect={() => {}}
    onCreateProject={onCreateProject} onCreateSession={onCreateSession}
  /></LanguageProvider>);
  const user = userEvent.setup();

  await user.click(screen.getByRole("button", { name: "新建项目" }));
  await user.type(screen.getByLabelText("项目名称"), "RNA Design");
  await user.click(screen.getByRole("button", { name: "创建项目" }));
  expect(onCreateProject).toHaveBeenCalledWith("RNA Design");

  await user.click(screen.getByRole("button", { name: "新建会话" }));
  await user.type(screen.getByLabelText("会话标题"), "First study");
  await user.click(screen.getByRole("button", { name: "创建会话" }));
  expect(onCreateSession).toHaveBeenCalledWith("First study");
});

it("downloads a workspace file and confirms deletion before calling the API", async () => {
  const onDownloadFile = vi.fn().mockResolvedValue(undefined);
  const onDeleteFile = vi.fn().mockResolvedValue(undefined);
  render(<LanguageProvider><ProjectSidebar
    projects={[{ id: "project-1", name: "Lab", description: "" }]}
    sessions={[]} files={[{ id: "file-1", name: "paper.txt", size: 512, status: "ready" }]}
    selectedProject="project-1" selected={null}
    user={{ id: "alice", name: "Alice", email: "alice@example.org" }}
    onSelectProject={() => {}} onSelect={() => {}}
    onDownloadFile={onDownloadFile} onDeleteFile={onDeleteFile}
  /></LanguageProvider>);
  const user = userEvent.setup();

  await user.click(screen.getByRole("button", { name: "下载 paper.txt" }));
  expect(onDownloadFile).toHaveBeenCalledWith("file-1", "paper.txt");

  await user.click(screen.getByRole("button", { name: "删除 paper.txt" }));
  expect(onDeleteFile).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "确认删除 paper.txt" }));
  expect(onDeleteFile).toHaveBeenCalledWith("file-1");
});
