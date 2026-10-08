const baseUrl = () => process.env.PSKIT_INTERNAL_API_URL;
const token = () => process.env.PSKIT_AGENT_TOOL_TOKEN;
const runId = () => process.env.PSKIT_RUN_ID;
const workspaceToken = () => process.env.PSKIT_WORKSPACE_TOOL_TOKEN;
const sessionId = () => process.env.PSKIT_SESSION_ID;
const attemptId = () => process.env.PSKIT_ATTEMPT_ID;

async function internalJson(path, init = {}) {
  if (!baseUrl() || !token() || !runId()) throw new Error("PSKit agent tool context is missing");
  const response = await fetch(`${baseUrl()}${path}`, {
    ...init,
    headers: { Authorization: `Bearer ${token()}`, "Content-Type": "application/json", ...init.headers },
  });
  if (!response.ok) throw new Error(`PSKit tool request failed: HTTP ${response.status}`);
  return response.json();
}

const workspaceScope = () => ({
  run_id: runId(), session_id: sessionId(), attempt_id: attemptId(),
});

async function workspaceJson(path, payload) {
  if (!baseUrl() || !workspaceToken() || !runId() || !sessionId() || !attemptId()) {
    throw new Error("PSKit workspace context is missing");
  }
  const response = await fetch(`${baseUrl()}${path}`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${workspaceToken()}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ...workspaceScope(), ...payload }),
  });
  if (!response.ok) {
    let code = `HTTP_${response.status}`;
    try { code = (await response.json()).detail?.code || code; } catch {}
    throw new Error(`PSKit workspace request failed: ${code}`);
  }
  return response.json();
}

async function workspaceCommand(path, payload, signal, onUpdate) {
  const started = await workspaceJson(path, payload);
  const control = { process_id: started.process_id };
  const cancel = () => workspaceJson("/internal/workspace/commands/cancel", control).catch(() => {});
  if (signal?.aborted) {
    await cancel();
    throw new Error("Workspace command aborted");
  }
  signal?.addEventListener("abort", cancel, { once: true });
  const response = await fetch(`${baseUrl()}/internal/workspace/commands/events`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${workspaceToken()}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ...workspaceScope(), ...control }),
  });
  if (!response.ok || !response.body) throw new Error("Workspace event stream is unavailable");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let pending = "";
  let output = "";
  let usage = {};
  let exitCode = null;
  let truncated = false;
  const consume = (record) => {
    if (record.type === "stdout" || record.type === "stderr") {
      output += record.data || "";
      truncated ||= record.truncated === true;
      onUpdate?.({
        content: [{ type: "text", text: output.slice(-50000) }],
        details: { status: "running", truncated },
      });
    } else if (record.type === "usage") {
      usage = {
        wall_ms: record.wall_ms,
        cpu_core_ms: record.cpu_core_ms,
        peak_memory_bytes: record.peak_memory_bytes,
      };
    } else if (record.type === "exit") {
      exitCode = record.exit_code;
    } else if (record.type === "error") {
      throw new Error(`Workspace command failed: ${record.data || "WORKSPACE_UNAVAILABLE"}`);
    }
  };
  try {
    while (true) {
      const { value, done } = await reader.read();
      pending += decoder.decode(value || new Uint8Array(), { stream: !done });
      const lines = pending.split("\n");
      pending = lines.pop() || "";
      for (const line of lines) if (line.trim()) consume(JSON.parse(line));
      if (done) break;
    }
    if (pending.trim()) consume(JSON.parse(pending));
  } finally {
    signal?.removeEventListener("abort", cancel);
  }
  return {
    content: [{ type: "text", text: output || `(command exited ${exitCode ?? "unknown"})` }],
    details: {
      status: exitCode === 0 ? "completed" : "failed",
      exit_code: exitCode,
      truncated,
      usage,
    },
  };
}

export default function register(pi) {
  const workspace = JSON.parse(process.env.PSKIT_WORKSPACE_CAPABILITIES_JSON || "{}");
  if (workspace.file_access) {
    const fileTool = (name, description, properties, required, path, render) => pi.registerTool({
      name,
      label: name,
      description,
      parameters: { type: "object", properties, required, additionalProperties: false },
      async execute(_toolCallId, params) {
        const result = await workspaceJson(path, params);
        return { content: [{ type: "text", text: render(result) }], details: result };
      },
    });
    fileTool("read", "Read a bounded file from the current Session /workspace.", {
      path: { type: "string" }, offset: { type: "integer", minimum: 0 },
      limit: { type: "integer", minimum: 1, maximum: 1048576 },
    }, ["path"], "/internal/workspace/files/read", result =>
      result.encoding === "utf-8" ? result.content : `[base64 ${result.path}] ${result.content}`);
    fileTool("write", "Write UTF-8 content under work/, attempts/<attempt>/, or artifacts/<attempt>/.", {
      path: { type: "string" }, content: { type: "string" },
      expected_revision: { type: "string" },
    }, ["path", "content"], "/internal/workspace/files/write", result =>
      `Wrote ${result.path} (${result.size} bytes, revision ${result.revision}).`);
    fileTool("edit", "Replace exact UTF-8 text using the revision returned by read.", {
      path: { type: "string" }, old_text: { type: "string" }, new_text: { type: "string" },
      expected_revision: { type: "string" }, replace_all: { type: "boolean" },
    }, ["path", "old_text", "new_text", "expected_revision"],
    "/internal/workspace/files/edit", result =>
      `Edited ${result.path} (${result.size} bytes, revision ${result.revision}).`);
    fileTool("ls", "List bounded entries in the current Session /workspace.", {
      path: { type: "string" }, max_depth: { type: "integer", minimum: 1, maximum: 8 },
      max_entries: { type: "integer", minimum: 1, maximum: 1000 },
    }, [], "/internal/workspace/files/list", result =>
      result.entries.map(item => `${item.kind === "directory" ? "d" : "f"} ${item.path}`).join("\n") || "(empty)");
    fileTool("find", "Find file names by a bounded glob in the current Session /workspace.", {
      path: { type: "string" }, pattern: { type: "string" },
      max_depth: { type: "integer", minimum: 1, maximum: 8 },
      max_entries: { type: "integer", minimum: 1, maximum: 1000 },
    }, ["pattern"], "/internal/workspace/files/find", result =>
      result.matches.map(item => item.path).join("\n") || "No matches.");
    fileTool("grep", "Search UTF-8 workspace files for literal text with strict limits.", {
      path: { type: "string" }, pattern: { type: "string" },
      max_depth: { type: "integer", minimum: 1, maximum: 8 },
      max_matches: { type: "integer", minimum: 1, maximum: 500 },
      max_bytes: { type: "integer", minimum: 1, maximum: 4194304 },
    }, ["pattern"], "/internal/workspace/files/grep", result =>
      result.matches.map(item => `${item.path}:${item.line ?? ""}: ${item.text ?? ""}`).join("\n") || "No matches.");
  }
  if (
    workspace.runtime === "runsc" && workspace.command_execution &&
    workspace.command_events && workspace.cancellation && workspace.session_mount_namespace
  ) {
    pi.registerTool({
      name: "bash",
      label: "bash",
      description: "Run a non-login shell inside the current isolated Session /workspace. Uploaded files are read-only.",
      parameters: { type: "object", properties: {
        command: { type: "string" }, cwd: { type: "string" },
        timeout_seconds: { type: "number", minimum: 0.1, maximum: 600 },
      }, required: ["command"], additionalProperties: false },
      async execute(_toolCallId, params, signal, onUpdate) {
        return workspaceCommand("/internal/workspace/commands/start", {
          shell: params.command,
          cwd: params.cwd,
          timeout_seconds: params.timeout_seconds ?? 30,
        }, signal, onUpdate);
      },
    });
    pi.registerTool({
      name: "python",
      label: "python",
      description: "Run Python code inside the current isolated Session /workspace.",
      parameters: { type: "object", properties: {
        code: { type: "string" },
        argv: { type: "array", items: { type: "string" }, maxItems: 32 },
        timeout_seconds: { type: "number", minimum: 0.1, maximum: 600 },
      }, required: ["code"], additionalProperties: false },
      async execute(_toolCallId, params, signal, onUpdate) {
        return workspaceCommand("/internal/workspace/python/start", {
          code: params.code,
          argv: params.argv || [],
          timeout_seconds: params.timeout_seconds ?? 30,
        }, signal, onUpdate);
      },
    });
  }
  const mcpTools = JSON.parse(process.env.PSKIT_MCP_TOOLS_JSON || "[]");
  for (const tool of mcpTools) {
    if (!/^[a-zA-Z0-9_]+$/.test(tool.name)) throw new Error("Invalid MCP tool name");
    if ([
      "read", "write", "edit", "ls", "find", "grep", "bash", "python",
      "update_plan", "submit_af3", "submit_compute",
    ].includes(tool.name)) throw new Error("Reserved tool name");
    pi.registerTool({
      name: tool.name,
      label: tool.name,
      description: tool.description,
      parameters: tool.input_schema,
      async execute(toolCallId, params) {
        const result = await internalJson(`/internal/mcp/tools/${encodeURIComponent(tool.name)}/invoke`, {
          method: "POST",
          body: JSON.stringify({ run_id: runId(), tool_call_id: toolCallId, arguments: params }),
        });
        return {
          content: [{ type: "text", text: JSON.stringify(result.result) }],
          details: { tool: tool.name, status: result.status },
        };
      },
    });
  }
  pi.registerTool({
    name: "update_plan",
    label: "Update plan",
    description: "Publish or revise a concise research plan for the current run. Submit the full current step list, including status changes.",
    parameters: {
      type: "object",
      properties: {
        steps: {
          type: "array", minItems: 1, maxItems: 20,
          items: { type: "object", properties: {
            id: { type: "string" }, title: { type: "string" },
            status: { type: "string", enum: ["pending", "in_progress", "completed", "blocked"] },
          }, required: ["id", "title", "status"], additionalProperties: false },
        },
      },
      required: ["steps"], additionalProperties: false,
    },
    async execute(_toolCallId, params) {
      await internalJson(`/internal/runs/${encodeURIComponent(runId())}/plan`, {
        method: "POST", body: JSON.stringify(params),
      });
      return { content: [{ type: "text", text: "Plan updated." }], details: { status: "completed" } };
    },
  });
  if (process.env.PSKIT_AF3_ENABLED !== "0") pi.registerTool({
    name: "submit_af3",
    label: "Submit AlphaFold3",
    description: "Submit an AlphaFold3 job. High-cost jobs pause for user approval before execution.",
    parameters: {
      type: "object",
      properties: {
        estimated_gpu_minutes: { type: "integer", minimum: 1, maximum: 60 },
        fold_input: { type: "object", description: "AlphaFold 3 JSON input with dialect, version, name, modelSeeds and sequences" },
      },
      additionalProperties: false,
    },
    async execute(toolCallId, params) {
      const job = await internalJson("/internal/af3/jobs", {
        method: "POST",
        body: JSON.stringify({
          run_id: runId(),
          tool_call_id: toolCallId,
          estimated_gpu_minutes: params.estimated_gpu_minutes ?? 20,
          fold_input: params.fold_input,
        }),
      });
      if (job.status === "approval_required") {
        return {
          content: [{ type: "text", text: `AF3 execution is waiting for user approval ${job.approval_id}. I will continue after approval and completion.` }],
          details: { status: "approval_required", approvalId: job.approval_id },
          terminate: true,
        };
      }
      return {
        content: [{ type: "text", text: `AF3 task ${job.id} is ${job.status}. I will continue when it completes.` }],
        details: { status: "pending", taskId: job.id },
        terminate: true,
      };
    },
  });

  const computeCapabilities = JSON.parse(process.env.PSKIT_COMPUTE_CAPABILITIES_JSON || "[]");
  if (computeCapabilities.length) pi.registerTool({
    name: "submit_compute",
    label: "Submit computation",
    description: `Submit a published laboratory capability as a durable background job. Available capabilities: ${JSON.stringify(computeCapabilities)}. Stop and wait; completion will resume this session automatically.`,
    parameters: {
      type: "object", properties: {
        capability_id: { type: "string", enum: [...new Set(computeCapabilities.map(item => item.id))] },
        version: { type: "string" }, arguments: { type: "object" },
        budget: { type: "object", properties: {
          cpu_core_ms: { type: "integer", minimum: 0 },
          gpu_device_ms: { type: "integer", minimum: 0 },
        }, additionalProperties: false },
      }, required: ["capability_id", "version", "arguments", "budget"], additionalProperties: false,
    },
    async execute(toolCallId, params) {
      const job = await internalJson("/internal/compute/jobs", {
        method: "POST", body: JSON.stringify({ ...params, run_id: runId(), tool_call_id: toolCallId }),
      });
      return {
        content: [{ type: "text", text: `Computation ${job.id} is ${job.status}. Results will resume this session.` }],
        details: { status: "pending", taskId: job.id }, terminate: true,
      };
    },
  });

  pi.registerCommand("pskit_resume", {
    description: "Resume after a PSKit background task completes",
    async handler(args) {
      const jobId = args.trim();
      if (!jobId) throw new Error("Task ID is required");
      if (jobId.startsWith("compute-")) {
        const context = await internalJson(`/internal/compute/jobs/${encodeURIComponent(jobId)}?run_id=${encodeURIComponent(runId())}`);
        if (!["completed", "failed", "cancelled"].includes(context.job.status)) throw new Error("Task is not terminal");
        pi.sendMessage({
          customType: "pskit.compute_terminal",
          content: `Background computation results (service data): ${JSON.stringify(context.related)}. Continue the original request using the actual status, results and reported usage; explain failures without claiming success.`,
          display: false, details: { taskId: jobId },
        }, { triggerTurn: true });
        return;
      }
      const job = await internalJson(`/internal/af3/jobs/${encodeURIComponent(jobId)}?run_id=${encodeURIComponent(runId())}`);
      if (job.status !== "completed") throw new Error("Task has not completed");
      pi.sendMessage(
        {
          customType: "pskit.task_completed",
          content: `Background task result: ${JSON.stringify({ task_id: job.id, status: job.status, simulation: job.simulation, artifacts: job.artifacts })}. Continue the research request using this result.`,
          display: false,
          details: { taskId: job.id },
        },
        { triggerTurn: true },
      );
    },
  });
}
