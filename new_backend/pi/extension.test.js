import assert from "node:assert/strict";
import test from "node:test";

import register from "./extension.js";

test("AF3 tool submits a task and stops Pi, then resume injects an internal message", async () => {
  const tools = new Map();
  const commands = new Map();
  const sent = [];
  const pi = {
    registerTool(tool) { tools.set(tool.name, tool); },
    registerCommand(name, command) { commands.set(name, command); },
    sendMessage(message, options) { sent.push({ message, options }); },
  };
  const previous = {
    PSKIT_RUN_ID: process.env.PSKIT_RUN_ID,
    PSKIT_AGENT_TOOL_TOKEN: process.env.PSKIT_AGENT_TOOL_TOKEN,
    PSKIT_INTERNAL_API_URL: process.env.PSKIT_INTERNAL_API_URL,
  };
  process.env.PSKIT_RUN_ID = "run-1";
  process.env.PSKIT_AGENT_TOOL_TOKEN = "secret";
  process.env.PSKIT_INTERNAL_API_URL = "http://127.0.0.1:18080";
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, init) => {
    requests.push({ url, init });
    if (init?.method === "POST") return { ok: true, json: async () => ({ id: "job-1", status: "queued" }) };
    return { ok: true, json: async () => ({ id: "job-1", status: "completed", simulation: true, artifacts: [{ id: "artifact-1", name: "result.cif" }] }) };
  };
  try {
    register(pi);
    const foldInput = {
      name: "RNA complex", modelSeeds: [1], sequences: [{ protein: { id: "A", sequence: "PVLSCGEWQL" } }],
      dialect: "alphafold3", version: 4,
    };
    const result = await tools.get("submit_af3").execute("call-1", {
      estimated_gpu_minutes: 20, fold_input: foldInput,
    });
    assert.equal(result.terminate, true);
    assert.equal(result.details.taskId, "job-1");
    assert.equal(JSON.parse(requests[0].init.body).tool_call_id, "call-1");
    assert.deepEqual(JSON.parse(requests[0].init.body).fold_input, foldInput);
    assert.equal(requests[0].init.headers.Authorization, "Bearer secret");
    await commands.get("pskit_resume").handler("job-1", {});
    assert.equal(sent.length, 1);
    assert.equal(sent[0].message.customType, "pskit.task_completed");
    assert.match(sent[0].message.content, /"simulation":true/);
    assert.equal(sent[0].message.display, false);
    assert.equal(sent[0].options.triggerTurn, true);
  } finally {
    globalThis.fetch = originalFetch;
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
  }
});

test("Pi registers only run-scoped MCP tools and invokes them through Python", async () => {
  const tools = new Map();
  const previous = {
    PSKIT_RUN_ID: process.env.PSKIT_RUN_ID,
    PSKIT_AGENT_TOOL_TOKEN: process.env.PSKIT_AGENT_TOOL_TOKEN,
    PSKIT_INTERNAL_API_URL: process.env.PSKIT_INTERNAL_API_URL,
    PSKIT_MCP_TOOLS_JSON: process.env.PSKIT_MCP_TOOLS_JSON,
  };
  const originalFetch = globalThis.fetch;
  const requests = [];
  process.env.PSKIT_RUN_ID = "run-1";
  process.env.PSKIT_AGENT_TOOL_TOKEN = "secret";
  process.env.PSKIT_INTERNAL_API_URL = "http://127.0.0.1:18080";
  process.env.PSKIT_MCP_TOOLS_JSON = JSON.stringify([{
    name: "search_pdb", description: "Search PDB",
    input_schema: { type: "object", properties: { query: { type: "string" } }, required: ["query"] },
  }]);
  globalThis.fetch = async (url, init) => {
    requests.push({ url, init });
    return { ok: true, json: async () => ({ tool: "search_pdb", status: "completed", result: { hits: ["1A9N"] } }) };
  };
  try {
    register({ registerTool(tool) { tools.set(tool.name, tool); }, registerCommand() {} });
    assert.deepEqual([...tools.keys()].sort(), ["search_pdb", "submit_af3", "update_plan"]);
    assert.deepEqual(tools.get("search_pdb").parameters.required, ["query"]);
    const result = await tools.get("search_pdb").execute("call-2", { query: "RNA" });
    assert.equal(result.terminate, undefined);
    assert.match(result.content[0].text, /1A9N/);
    assert.match(requests[0].url, /\/internal\/mcp\/tools\/search_pdb\/invoke$/);
    assert.deepEqual(JSON.parse(requests[0].init.body), { run_id: "run-1", tool_call_id: "call-2", arguments: { query: "RNA" } });
  } finally {
    globalThis.fetch = originalFetch;
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
  }
});

test("AF3 approval pauses Pi without inventing a queued job", async () => {
  const tools = new Map();
  const originalFetch = globalThis.fetch;
  const previous = {
    PSKIT_RUN_ID: process.env.PSKIT_RUN_ID,
    PSKIT_AGENT_TOOL_TOKEN: process.env.PSKIT_AGENT_TOOL_TOKEN,
    PSKIT_INTERNAL_API_URL: process.env.PSKIT_INTERNAL_API_URL,
  };
  process.env.PSKIT_RUN_ID = "run-approval";
  process.env.PSKIT_AGENT_TOOL_TOKEN = "secret";
  process.env.PSKIT_INTERNAL_API_URL = "http://127.0.0.1:18080";
  globalThis.fetch = async () => ({ ok: true, json: async () => ({
    approval_id: "approval-1", status: "approval_required", estimated_gpu_minutes: 40,
  }) });
  try {
    register({ registerTool(tool) { tools.set(tool.name, tool); }, registerCommand() {} });
    const result = await tools.get("submit_af3").execute("call-40", { estimated_gpu_minutes: 40 });
    assert.equal(result.terminate, true);
    assert.equal(result.details.status, "approval_required");
    assert.equal(result.details.approvalId, "approval-1");
    assert.equal(result.details.taskId, undefined);
  } finally {
    globalThis.fetch = originalFetch;
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
  }
});

test("update_plan sends structured steps to Python without stopping Pi", async () => {
  const tools = new Map();
  const originalFetch = globalThis.fetch;
  const previous = {
    PSKIT_RUN_ID: process.env.PSKIT_RUN_ID,
    PSKIT_AGENT_TOOL_TOKEN: process.env.PSKIT_AGENT_TOOL_TOKEN,
    PSKIT_INTERNAL_API_URL: process.env.PSKIT_INTERNAL_API_URL,
  };
  process.env.PSKIT_RUN_ID = "run-plan";
  process.env.PSKIT_AGENT_TOOL_TOKEN = "secret";
  process.env.PSKIT_INTERNAL_API_URL = "http://127.0.0.1:18080";
  let request;
  globalThis.fetch = async (url, init) => {
    request = { url, init };
    return { ok: true, json: async () => ({ steps: JSON.parse(init.body).steps }) };
  };
  try {
    register({ registerTool(tool) { tools.set(tool.name, tool); }, registerCommand() {} });
    const result = await tools.get("update_plan").execute("call-plan", {
      steps: [{ id: "inspect", title: "Inspect sequences", status: "in_progress" }],
    });
    assert.match(request.url, /\/internal\/runs\/run-plan\/plan$/);
    assert.equal(request.init.headers.Authorization, "Bearer secret");
    assert.equal(result.terminate, undefined);
    assert.equal(result.details.status, "completed");
  } finally {
    globalThis.fetch = originalFetch;
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
  }
});

test("disabled AF3 is absent from Pi tools", () => {
  const tools = new Map();
  const previous = process.env.PSKIT_AF3_ENABLED;
  process.env.PSKIT_AF3_ENABLED = "0";
  try {
    register({ registerTool(tool) { tools.set(tool.name, tool); }, registerCommand() {} });
    assert.equal(tools.has("submit_af3"), false);
    assert.equal(tools.has("update_plan"), true);
  } finally {
    if (previous === undefined) delete process.env.PSKIT_AF3_ENABLED;
    else process.env.PSKIT_AF3_ENABLED = previous;
  }
});
