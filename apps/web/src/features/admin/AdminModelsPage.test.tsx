import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "../../app/App";
import type { AdminModel } from "../../api/admin";
import { administrator, json, modelRow, setupAdmin } from "./adminTestUtils";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.localStorage.clear(); window.history.replaceState(null, "", "/"); });

it("publishes a saved model draft using its current revision and displays the server state", async () => {
  let row = structuredClone(modelRow);
  setupAdmin("/admin/models", (path, method, body) => {
    if (path.endsWith("/llm-aliases") && method === "GET") return json({ items: [row], next_cursor: null });
    if (path.endsWith("/lab-chat/publish")) {
      expect(body).toEqual({ expected_revision: 1, reason: "审核通过并发布" });
      row = { ...row, revision: 2, state: "published", published: row.draft };
      return json(row);
    }
  });
  render(<App />);
  const actor = userEvent.setup();
  await actor.click(await screen.findByRole("button", { name: "编辑 lab-chat" }));
  await actor.type(screen.getByLabelText("变更原因"), "审核通过并发布");
  await actor.click(screen.getByRole("button", { name: "发布模型" }));
  expect(await screen.findByText("已发布")).toBeInTheDocument();
});

it("retires a published alias using an audited revision request", async () => {
  let row: AdminModel = { ...modelRow, state: "published", published: modelRow.draft };
  setupAdmin("/admin/models", (path, method, body) => {
    if (path.endsWith("/llm-aliases") && method === "GET") return json({ items: [row], next_cursor: null });
    if (path.endsWith("/lab-chat/retire")) {
      expect(body).toEqual({ expected_revision: 1, reason: "下线停止新的模型提交" });
      row = { ...row, revision: 2, state: "retired" };
      return json(row);
    }
  });
  render(<App />);
  const actor = userEvent.setup();
  await actor.click(await screen.findByRole("button", { name: "编辑 lab-chat" }));
  await actor.type(screen.getByLabelText("变更原因"), "下线停止新的模型提交");
  await actor.click(screen.getByRole("button", { name: "下线模型" }));
  expect(await screen.findByText("模型已下线")).toBeInTheDocument();
  expect(await screen.findByText("已下线")).toBeInTheDocument();
});

it("retains the model draft and provides a localized revision conflict after a rejected save", async () => {
  setupAdmin("/admin/models", (path, method, body) => {
    if (path.endsWith("/llm-aliases") && method === "GET") return json({ items: [modelRow], next_cursor: null });
    if (path.endsWith("/lab-chat/draft")) {
      expect(body).toEqual({ expected_revision: 1, reason: "调整实验成员授权", allowed_user_ids: ["bob"], allowed_group_ids: [], purposes: ["chat"], supports_images: false, reasoning_levels: [], default_for_purposes: [] });
      return json({ detail: { code: "REVISION_CONFLICT" } }, 409);
    }
  });
  render(<App />);
  const actor = userEvent.setup();
  await actor.click(await screen.findByRole("button", { name: "编辑 lab-chat" }));
  await actor.clear(screen.getByLabelText("允许的用户 ID"));
  await actor.type(screen.getByLabelText("允许的用户 ID"), "bob");
  await actor.type(screen.getByLabelText("变更原因"), "调整实验成员授权");
  await actor.click(screen.getByRole("button", { name: "保存草稿" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("已被其他人修改");
  expect(screen.getByLabelText("允许的用户 ID")).toHaveValue("bob");
  expect(screen.getByLabelText("变更原因")).toHaveValue("调整实验成员授权");
});

it("removes privileged actions and refreshes current permissions when a write returns 403", async () => {
  let revoked = false;
  setupAdmin("/admin/models", (path, method) => {
    if (path.endsWith("/admin/me") && revoked) return json({ ...administrator, roles: ["auditor"], permissions: ["models:read"] });
    if (path.endsWith("/llm-aliases") && method === "GET") return json({ items: [modelRow], next_cursor: null });
    if (path.endsWith("/lab-chat/draft")) { revoked = true; return json({ detail: { code: "ADMIN_FORBIDDEN" } }, 403); }
  });
  render(<App />);
  const actor = userEvent.setup();
  await actor.click(await screen.findByRole("button", { name: "编辑 lab-chat" }));
  await actor.type(screen.getByLabelText("变更原因"), "调整实验成员授权");
  await actor.click(screen.getByRole("button", { name: "保存草稿" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "发布模型" })).not.toBeInTheDocument());
  expect(screen.queryByRole("button", { name: "保存草稿" })).not.toBeInTheDocument();
  expect(await screen.findByRole("alert")).toHaveTextContent("权限");
});
