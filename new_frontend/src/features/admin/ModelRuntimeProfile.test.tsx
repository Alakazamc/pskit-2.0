import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "../../app/App";
import type { AdminModel, ModelDraft } from "../../api/admin";
import { administrator, json, modelRow, setupAdmin } from "./adminTestUtils";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); localStorage.clear(); window.history.replaceState(null, "", "/"); });

it("saves an explicit capability declaration and qualifies only through the protected server endpoint", async () => {
  let row: AdminModel = structuredClone(modelRow);
  const saves: ModelDraft[] = [];
  const qualifications: Record<string, unknown>[] = [];
  setupAdmin("/admin/models", (path, method, body) => {
    if (path.endsWith("/llm-aliases") && method === "GET") return json({ items: [row], next_cursor: null });
    if (path.endsWith("/lab-chat/draft")) { saves.push(body as ModelDraft); row = { ...row, revision: 2, draft: { ...body as ModelDraft, runtime_profile: { ...body.runtime_profile as NonNullable<ModelDraft["runtime_profile"]>, revision: 2, qualification_status: "untested" } } }; return json(row); }
    if (path.endsWith("/lab-chat/qualify")) { qualifications.push(body); return json({ detail: { code: "MODEL_QUALIFIER_UNAVAILABLE" } }, 422); }
  });
  render(<App />); const actor = userEvent.setup();
  await actor.click(await screen.findByRole("button", { name: "编辑 lab-chat" }));
  await actor.click(screen.getByRole("button", { name: "声明模型能力" }));
  expect(screen.getByLabelText("运行协议")).toHaveValue("chat_completions");
  expect(screen.getByLabelText("运行协议")).toHaveAttribute("readonly");
  await actor.clear(screen.getByLabelText("模型显示名")); await actor.type(screen.getByLabelText("模型显示名"), "Lab assistant");
  await actor.type(screen.getByLabelText("提供商显示名"), "Lab provider");
  await actor.click(screen.getByLabelText("声明支持工具调用"));
  await actor.click(screen.getByLabelText("声明支持流式工具调用"));
  await actor.click(within(screen.getByRole("group", { name: "声明的推理等级" })).getByRole("checkbox", { name: "high" }));
  await actor.type(screen.getByLabelText("变更原因"), "声明并检查实验模型能力");
  expect(screen.getByRole("button", { name: "执行模型验收" })).toBeDisabled();
  await actor.click(screen.getByRole("button", { name: "保存草稿" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "执行模型验收" })).toBeEnabled());
  expect(saves[0].runtime_profile).toMatchObject({ alias: "lab-chat", protocol: "chat_completions", display_name: "Lab assistant", provider_label: "Lab provider", supports_tools: true, supports_tool_streaming: true, reasoning_levels: ["high"], wire_policy: { reasoning_mode: "reasoning_effort" } });
  expect(saves[0].runtime_profile).not.toHaveProperty("qualification_evidence");
  await actor.click(screen.getByRole("button", { name: "执行模型验收" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("尚未启用有预算限制的模型验收器");
  expect(qualifications).toEqual([{ expected_revision: 2, reason: "声明并检查实验模型能力" }]);
  expect(screen.getByText("尚未验收")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "发布模型" })).toBeDisabled();
});

it("preserves a published runtime profile during ordinary edits and shows incomplete evidence without declaring readiness", async () => {
  const profile = { alias: "lab-chat", revision: 3, protocol: "chat_completions" as const, display_name: "Reviewed lab model", provider_label: "Reviewed provider", supports_tools: true, supports_tool_streaming: true, qualification_status: "degraded" as const,
    wire_policy: { stream_usage_mode: "not_supported" as const }, qualification_evidence: { evidence_id: "e1", alias: "lab-chat", profile_revision: 3, profile_fingerprint: "a".repeat(64), deployment_fingerprint: "b".repeat(64), provider_url_fingerprint: "c".repeat(64), pi_version: "test-pi", gateway_version: "test-gateway", checked_at: "2026-10-10T00:00:00Z", verification_scope: "offline" as const, status: "degraded" as const,
      cases: [{ case_id: "workspace_artifact", status: "unknown" as const, request_fingerprint: "d".repeat(64) }] } };
  const row = { ...modelRow, draft: { ...modelRow.draft!, runtime_profile: profile } };
  let saved: Record<string, unknown> | undefined;
  setupAdmin("/admin/models", (path, method, body) => {
    if (path.endsWith("/llm-aliases") && method === "GET") return json({ items: [row], next_cursor: null });
    if (path.endsWith("/lab-chat/draft")) { saved = body; return json(row); }
  });
  render(<App />); const actor = userEvent.setup();
  await actor.click(await screen.findByRole("button", { name: "编辑 lab-chat" }));
  expect(screen.getByText("workspace_artifact: unknown")).toBeInTheDocument();
  expect(screen.getByText(/验收不完整/)).toBeInTheDocument(); expect(screen.getByText("验证范围：offline")).toBeInTheDocument();
  expect(screen.getByLabelText("模型显示名")).toHaveValue("Reviewed lab model");
  await actor.type(screen.getByLabelText("变更原因"), "保持运行能力修改成员");
  await actor.click(screen.getByRole("button", { name: "保存草稿" }));
  await waitFor(() => expect(saved).toBeDefined());
  expect(saved?.runtime_profile).toEqual(profile);
});

it("does not expose qualification actions to readers without model publication permission", async () => {
  setupAdmin("/admin/models", (path) => path.endsWith("/llm-aliases") ? json({ items: [modelRow], next_cursor: null }) : undefined,
    { ...administrator, permissions: ["models:read"] });
  render(<App />); await userEvent.setup().click(await screen.findByRole("button", { name: "查看 lab-chat" }));
  expect(screen.queryByRole("button", { name: "执行模型验收" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "声明模型能力" })).toBeDisabled();
});
