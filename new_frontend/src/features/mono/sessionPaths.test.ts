import { expect, it } from "vitest";
import { personalSessionPath, projectPath, projectSessionPath, sessionPath } from "./sessionPaths";

it("builds readable browser URLs for personal and project conversations", () => {
  expect(personalSessionPath("session-first")).toBe("/session/session-first");
  expect(projectPath("project-lab")).toBe("/p/project-lab");
  expect(projectSessionPath("project-lab", "session-lab")).toBe("/p/project-lab/c/session-lab");
  expect(sessionPath("project-alice", "project-alice", "session-first")).toBe("/session/session-first");
  expect(sessionPath("project-lab", "project-alice", "session-lab")).toBe("/p/project-lab/c/session-lab");
});
