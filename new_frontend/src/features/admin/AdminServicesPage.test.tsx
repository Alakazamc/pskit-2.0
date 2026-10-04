import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "../../app/App";
import { json, serviceRow, setupAdmin } from "./adminTestUtils";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.localStorage.clear(); window.history.replaceState(null, "", "/"); });

it("keeps an auditor's visible service read-only when its owner is outside the maintainer grant", async () => {
  setupAdmin("/admin/services", (path, method) => {
    if (path.endsWith("/services") && method === "GET") return json({ items: [{ ...serviceRow, owner_user_id: "bob" }], next_cursor: null });
  }, { user_id: "alice", roles: ["service_maintainer", "auditor"], permissions: ["services:read", "services:write"], service_ids: ["rna"] });
  render(<App />);
  const actor = userEvent.setup();
  const open = await screen.findByRole("button", { name: /RNA/ });
  expect(open).toHaveAccessibleName("查看 RNA");
  await actor.click(open);
  expect(screen.getByLabelText("服务名称")).toBeDisabled();
  expect(screen.queryByRole("button", { name: "保存草稿" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "连接检查" })).not.toBeInTheDocument();
});

it("lets a maintainer save an owned service draft without a release or publish action", async () => {
  let row = structuredClone(serviceRow);
  setupAdmin("/admin/services", (path, method, body) => {
    if (path.endsWith("/services") && method === "GET") return json({ items: [row], next_cursor: null });
    if (path.endsWith("/rna/draft")) {
      expect(body).toEqual({ expected_revision: 1, reason: "修订同门服务说明", name: "RNA updated", owner_user_id: "alice", transport: "worker_pull", endpoint_ref: "lab/rna", credential_ref: "service/rna", model_version: "weights-v1", capabilities: serviceRow.capabilities });
      row = { ...row, name: "RNA updated", revision: 2 };
      return json(row);
    }
  }, { user_id: "alice", roles: ["service_maintainer"], permissions: ["services:read", "services:write", "models:read", "jobs:read"], service_ids: ["rna"] });
  render(<App />);
  const actor = userEvent.setup();
  await actor.click(await screen.findByRole("button", { name: "编辑 RNA" }));
  await actor.clear(screen.getByLabelText("服务名称"));
  await actor.type(screen.getByLabelText("服务名称"), "RNA updated");
  await actor.type(screen.getByLabelText("变更原因"), "修订同门服务说明");
  await actor.click(screen.getByRole("button", { name: "保存草稿" }));
  expect(await screen.findByRole("rowheader", { name: /RNA updated/ })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "创建发布包" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "发布配置" })).not.toBeInTheDocument();
});

it("validates saved schema before enabling a release and blocks checks for unsaved changes", async () => {
  let row = structuredClone(serviceRow);
  setupAdmin("/admin/services", (path, method, body) => {
    if (path.endsWith("/services") && method === "GET") return json({ items: [row], next_cursor: null });
    if (path.endsWith("/rna/checks")) {
      expect(body).toEqual({ expected_revision: 1, reason: "核对已保存能力定义", kind: "schema" });
      row = { ...row, state: "validated", schema_digest: "sha256:verified" };
      return json({ service_id: "rna", revision: 1, kind: "schema", status: "passed", checked_at: "2026-10-04T10:00:00Z", message: "Schema matches", schema_digest: row.schema_digest, discovered_capabilities: [] });
    }
  });
  render(<App />);
  const actor = userEvent.setup();
  await actor.click(await screen.findByRole("button", { name: "编辑 RNA" }));
  await actor.type(screen.getByLabelText("变更原因"), "核对已保存能力定义");
  expect(screen.getByRole("button", { name: "创建发布包" })).toBeDisabled();
  await actor.type(screen.getByLabelText("服务名称"), " changed");
  expect(screen.getByRole("button", { name: "验证 Schema" })).toBeDisabled();
  await actor.clear(screen.getByLabelText("服务名称"));
  await actor.type(screen.getByLabelText("服务名称"), "RNA");
  await actor.click(screen.getByRole("button", { name: "验证 Schema" }));
  expect(await screen.findByText("Schema matches")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "创建发布包" })).toBeEnabled();
});

it("shows connectivity check failure without claiming that a model ran successfully", async () => {
  setupAdmin("/admin/services", (path, method, body) => {
    if (path.endsWith("/services") && method === "GET") return json({ items: [serviceRow], next_cursor: null });
    if (path.endsWith("/rna/checks")) {
      expect(body).toEqual({ expected_revision: 1, reason: "检查服务连接状态", kind: "connectivity" });
      return json({ service_id: "rna", revision: 1, kind: "connectivity", status: "failed", checked_at: "2026-10-04T10:00:00Z", message: "Connection unavailable", schema_digest: null, discovered_capabilities: [] });
    }
  });
  render(<App />);
  const actor = userEvent.setup();
  await actor.click(await screen.findByRole("button", { name: "编辑 RNA" }));
  await actor.type(screen.getByLabelText("变更原因"), "检查服务连接状态");
  await actor.click(screen.getByRole("button", { name: "连接检查" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("检查未通过");
  expect(screen.getByText("Connection unavailable")).toBeInTheDocument();
});

it("shows a configuration release impact before publishing its immutable service revision", async () => {
  setupAdmin("/admin/services", (path, method, body) => {
    if (path.endsWith("/services") && method === "GET") return json({ items: [{ ...serviceRow, state: "validated" }], next_cursor: null });
    if (path.endsWith("/config-releases")) {
      expect(body).toEqual({ expected_revision: 0, reason: "发布已审核科研能力", services: [{ service_id: "rna", revision: 1 }] });
      return json({ release_id: "release-1", revision: 1, state: "draft", services: [{ service_id: "rna", revision: 1 }], impact: ["rna.predict@1"] });
    }
    if (path.endsWith("/release-1/publish")) {
      expect(body).toEqual({ expected_revision: 1, reason: "发布已审核科研能力" });
      return json({ release_id: "release-1", revision: 2, state: "published", services: [{ service_id: "rna", revision: 1 }], impact: ["rna.predict@1"] });
    }
  });
  render(<App />);
  const actor = userEvent.setup();
  await actor.click(await screen.findByRole("button", { name: "编辑 RNA" }));
  await actor.type(screen.getByLabelText("变更原因"), "发布已审核科研能力");
  await actor.click(screen.getByRole("button", { name: "创建发布包" }));
  expect(await screen.findByText("rna.predict@1")).toBeInTheDocument();
  await actor.click(screen.getByRole("button", { name: "发布配置" }));
  expect(await screen.findByText("配置已发布")).toBeInTheDocument();
});
