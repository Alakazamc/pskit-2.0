import { cleanup, render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "../../app/App";
import { administrator, auditRow, jobRow, json, sandboxRow, setupAdmin, userRow } from "./adminTestUtils";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.localStorage.clear(); window.history.replaceState(null, "", "/"); });

it("edits quota limits in display units while retaining used and reserved counters", async () => {
  let row = structuredClone(userRow);
  setupAdmin("/admin/users", (path, method, body) => {
    if (path.endsWith("/users") && method === "GET") return json({ items: [row], next_cursor: null });
    if (path.endsWith("/alice/limits") && method === "PUT") {
      expect(body).toEqual({ expected_revision: 2, reason: "调整实验资源上限", token_monthly_limit: 100000, gpu_daily_minutes: 30, cpu_daily_core_ms: 45000, concurrency_limit: 2, storage_limit_bytes: 1048576 });
      row = { ...row, revision: 3, gpu_daily_minutes: 30, cpu_daily_core_ms: 45000, gpu: { ...row.gpu, limit: 30, remaining: 10 }, cpu: { ...row.cpu, limit: 45000, remaining: 15000 } };
      return json(row);
    }
  });
  render(<App />);
  const actor = userEvent.setup();
  await actor.click(await screen.findByRole("button", { name: "编辑 alice" }));
  await actor.clear(screen.getByLabelText("GPU 每日分钟"));
  await actor.type(screen.getByLabelText("GPU 每日分钟"), "30");
  await actor.clear(screen.getByLabelText("CPU 每日核秒"));
  await actor.type(screen.getByLabelText("CPU 每日核秒"), "45");
  await actor.type(screen.getByLabelText("变更原因"), "调整实验资源上限");
  await actor.click(screen.getByRole("button", { name: "保存额度" }));
  expect(await screen.findByText("已保存")).toBeInTheDocument();
  const gpu = within(screen.getByRole("group", { name: "GPU 分钟" }));
  expect(gpu.getByText("12")).toBeInTheDocument();
  expect(gpu.getByText("8")).toBeInTheDocument();
  expect(gpu.getByText("10")).toBeInTheDocument();
  expect(within(screen.getByRole("group", { name: "CPU 核秒" })).getByText("18")).toBeInTheDocument();
});

it("keeps an unlimited storage policy explicit until an operator enters a new limit", async () => {
  setupAdmin("/admin/users", (path) => path.endsWith("/users") ? json({ items: [{ ...userRow, storage_limit_bytes: null }], next_cursor: null }) : undefined);
  render(<App />);
  const actor = userEvent.setup();
  await actor.click(await screen.findByRole("button", { name: "编辑 alice" }));
  expect(screen.getByLabelText("存储字节上限")).toHaveValue(null);
  expect(screen.getByText("不限额")).toBeInTheDocument();
  await actor.type(screen.getByLabelText("变更原因"), "调整实验资源上限");
  expect(screen.getByRole("button", { name: "保存额度" })).toBeDisabled();
});

it("shows cancel requested until a refreshed job confirms execution stopped", async () => {
  let row = structuredClone(jobRow);
  setupAdmin("/admin/jobs", (path, method, body) => {
    if (path.endsWith("/jobs") && method === "GET") return json({ items: [row], next_cursor: null });
    if (path.endsWith("/compute-1/cancel")) {
      expect(body).toEqual({ expected_revision: 1, reason: "终止不再需要的计算" });
      row = { ...row, revision: 2, status: "cancelling", cancellation_state: "requested" };
      return json({ operation_id: "op-1", resource_id: row.job_id, kind: "cancel", state: "requested", revision: 2 });
    }
  });
  render(<App />);
  const actor = userEvent.setup();
  await actor.click(await screen.findByRole("button", { name: "取消 compute-1" }));
  await actor.type(screen.getByLabelText("变更原因"), "终止不再需要的计算");
  await actor.click(screen.getByRole("button", { name: "请求取消" }));
  expect(await screen.findByRole("status", { name: "" })).toHaveTextContent("已请求停止");
  expect(screen.queryByText("已确认停止")).not.toBeInTheDocument();
  row = { ...row, revision: 3, status: "cancelled", cancellation_state: "confirmed", accounting_status: "settled" };
  await actor.click(screen.getByRole("button", { name: "刷新" }));
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("已确认停止"));
});

it("shows a drain request while the sandbox and its active session still run", async () => {
  setupAdmin("/admin/sandboxes", (path, method, body) => {
    if (path.endsWith("/sandboxes") && method === "GET") return json({ items: [sandboxRow], next_cursor: null });
    if (path.endsWith("/alice/drain")) {
      expect(body).toEqual({ expected_revision: 1, reason: "维护用户运行环境" });
      return json({ operation_id: "drain-1", resource_id: "alice", kind: "drain", state: "requested", revision: 2 });
    }
  });
  render(<App />);
  const actor = userEvent.setup();
  await actor.click(await screen.findByRole("button", { name: "排空 alice" }));
  await actor.type(screen.getByLabelText("变更原因"), "维护用户运行环境");
  await actor.click(screen.getByRole("button", { name: "请求排空" }));
  expect(await screen.findByRole("status")).toHaveTextContent("已请求停止");
  expect(screen.getAllByText("运行中").length).toBeGreaterThan(0);
  expect(screen.queryByText("已停止")).not.toBeInTheDocument();
  expect(screen.getByText("session-1")).toBeInTheDocument();
});

it("requires stop evidence to reconcile usage and preserves unknown metrics as null", async () => {
  setupAdmin("/admin/usage", (path, method, body) => {
    if (path.endsWith("/usage/reconciliation") && method === "GET") return json({ items: [{ ...jobRow, accounting_status: "pending_reconciliation" }], next_cursor: null });
    if (path.endsWith("/compute-1/reconcile")) {
      expect(body).toEqual({ expected_revision: 1, reason: "补录维护者实际计量", evidence: "worker report confirms execution stopped", stopped: true, terminal_status: "completed", usage: { wall_ms: null, cpu_core_ms: null, gpu_device_ms: 6000, peak_memory_bytes: null, peak_gpu_memory_bytes: null, gpu_count: null, source: "service_reported" } });
      return json({ ...jobRow, revision: 2, status: "completed", accounting_status: "settled" });
    }
  });
  render(<App />);
  const actor = userEvent.setup();
  await actor.click(await screen.findByRole("button", { name: "对账 compute-1" }));
  await actor.type(screen.getByLabelText("GPU 设备毫秒"), "6000");
  await actor.selectOptions(screen.getByLabelText("计量来源"), "service_reported");
  await actor.type(screen.getByLabelText("停止证据"), "worker report confirms execution stopped");
  await actor.type(screen.getByLabelText("变更原因"), "补录维护者实际计量");
  expect(screen.getByRole("button", { name: "提交对账" })).toBeDisabled();
  await actor.click(screen.getByLabelText("已核实执行停止"));
  await actor.click(screen.getByRole("button", { name: "提交对账" }));
  expect(await screen.findByText("已保存")).toBeInTheDocument();
});

it.each(["zh", "en"] as const)("reconciles legacy AF3 in its native minutes with explicit stop evidence in %s", async (language) => {
  const row = { ...jobRow, job_id: "legacy-af3", service_id: null, capability_id: "af3", status: "cancelled", accounting_status: "pending_reconciliation", cancellation_state: "requested" as const };
  let submitted = false;
  setupAdmin("/admin/usage", (path, method, body) => {
    if (path.endsWith("/usage/reconciliation") && method === "GET") return json({ items: [row], next_cursor: null });
    if (path === "/api/v1/admin/af3/jobs/legacy-af3/reconcile" && method === "POST") {
      expect(body).toEqual({ expected_revision: 1, reason: "Verified AF3 worker logs", evidence: "AF3 process exited and accounting checked", stopped: true, actual_gpu_minutes: 7, source: "legacy_wall" });
      submitted = true;
      return json({ ...row, revision: 2, accounting_status: "settled", cancellation_state: "confirmed" });
    }
  }, administrator, language);
  render(<App />);
  const actor = userEvent.setup();
  const zh = language === "zh";
  await actor.click(await screen.findByRole("button", { name: `${zh ? "对账" : "Reconcile"} legacy-af3` }));
  const fields = screen.getAllByRole("spinbutton");
  expect(fields).toHaveLength(1);
  expect(fields[0]).toHaveAccessibleName(zh ? "AF3 实际计费分钟" : "AF3 actual billed minutes");
  await actor.type(fields[0], "7");
  await actor.type(screen.getByLabelText(zh ? "停止证据" : "Stop evidence"), "AF3 process exited and accounting checked");
  await actor.type(screen.getByLabelText(zh ? "变更原因" : "Change reason"), "Verified AF3 worker logs");
  const save = screen.getByRole("button", { name: zh ? "提交对账" : "Submit reconciliation" });
  expect(save).toBeDisabled();
  await actor.click(screen.getByLabelText(zh ? "已核实执行停止" : "Execution stop verified"));
  await actor.click(save);
  expect(await screen.findByText(zh ? "已保存" : "Saved")).toBeInTheDocument();
  expect(submitted).toBe(true);
});

it.each(["users", "jobs", "sandboxes", "usage"])("gives auditors read-only %s controls", async (section) => {
  setupAdmin(`/admin/${section}`, (path) => {
    if (path.endsWith("/users")) return json({ items: [userRow], next_cursor: null });
    if (path.endsWith("/jobs")) return json({ items: [jobRow], next_cursor: null });
    if (path.endsWith("/sandboxes")) return json({ items: [sandboxRow], next_cursor: null });
    if (path.endsWith("/usage/reconciliation")) return json({ items: [jobRow], next_cursor: null });
  }, { ...administrator, roles: ["auditor"], permissions: administrator.permissions.filter((permission) => permission.endsWith(":read")) });
  render(<App />);
  await screen.findByRole("button", { name: /^查看 / });
  expect(screen.queryByRole("button", { name: /保存额度|请求取消|请求排空|提交对账|^编辑 |^取消 |^排空 |^对账 / })).not.toBeInTheDocument();
});

it.each(["zh", "en"] as const)("handles empty and paged audit lists in %s", async (language) => {
  let populated = false;
  setupAdmin("/admin/audit", (path, _method, _body, search) => {
    if (!path.endsWith("/audit-events")) return;
    if (!populated) return json({ items: [], next_cursor: null });
    if (!search.has("cursor")) return json({ items: [auditRow], next_cursor: "opaque cursor+1" });
    expect(search.get("cursor")).toBe("opaque cursor+1");
    return json({ items: [{ ...auditRow, event_id: "audit-2", action: "service.published" }], next_cursor: null });
  }, administrator, language);
  render(<App />);
  const actor = userEvent.setup();
  expect(await screen.findByText(language === "zh" ? "暂无记录。" : "No records yet.")).toBeInTheDocument();
  populated = true;
  await actor.click(screen.getByRole("button", { name: language === "zh" ? "刷新" : "Refresh" }));
  expect(await screen.findByText("quota.updated")).toBeInTheDocument();
  await actor.click(screen.getByRole("button", { name: language === "zh" ? "加载更多" : "Load more" }));
  expect(await screen.findByText("service.published")).toBeInTheDocument();
  await waitFor(() => expect(screen.queryByRole("button", { name: language === "zh" ? "加载更多" : "Load more" })).not.toBeInTheDocument());
});
