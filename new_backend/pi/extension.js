const baseUrl = () => process.env.PSKIT_INTERNAL_API_URL;
const token = () => process.env.PSKIT_AGENT_TOOL_TOKEN;
const runId = () => process.env.PSKIT_RUN_ID;

async function internalJson(path, init = {}) {
  if (!baseUrl() || !token() || !runId()) throw new Error("PSKit agent tool context is missing");
  const response = await fetch(`${baseUrl()}${path}`, {
    ...init,
    headers: { Authorization: `Bearer ${token()}`, "Content-Type": "application/json", ...init.headers },
  });
  if (!response.ok) throw new Error(`PSKit tool request failed: HTTP ${response.status}`);
  return response.json();
}

export default function register(pi) {
  const mcpTools = JSON.parse(process.env.PSKIT_MCP_TOOLS_JSON || "[]");
  for (const tool of mcpTools) {
    if (!/^[a-zA-Z0-9_]+$/.test(tool.name)) throw new Error("Invalid MCP tool name");
    if (["update_plan", "submit_af3", "submit_compute"].includes(tool.name)) throw new Error("Reserved tool name");
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
