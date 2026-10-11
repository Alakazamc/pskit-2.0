"""Characterize the pinned Pi parser with an in-memory fetch, no socket/provider."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from app.domain.admin.model_qualification import ModelQualification
from tests.test_model_qualification_matrix import tool_stream


@pytest.mark.parametrize("split_name", [False, True])
def test_pinned_pi_retains_tool_identity_and_only_appends_arguments(split_name):
    pi_root = Path(os.environ.get("PSKIT_TEST_PI_ROOT", str(Path(__file__).parents[1] / "pi")))
    package_root = pi_root / "node_modules/@earendil-works/pi-coding-agent"
    package = json.loads((package_root / "package.json").read_text())
    assert package["version"] == "0.87.1"
    module = package_root / "node_modules/@earendil-works/pi-ai/dist/api/openai-completions.js"
    document = tool_stream()
    first = document["chunks"][0]["choices"][0]["delta"]["tool_calls"][0]
    last = document["chunks"][1]["choices"][0]["delta"]["tool_calls"][0]
    last["id"] = "stable-call"
    if split_name:
        first["function"]["name"] = "qualification_"
        last["function"]["name"] = "echo"
    else:
        last["function"]["name"] = "qualification_echo"
    raw = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in document["chunks"]) + "data: [DONE]\n\n"
    source = r'''
const { stream } = await import(process.argv[1]);
const input = JSON.parse(await new Promise(resolve => {
  let text = ""; process.stdin.setEncoding("utf8");
  process.stdin.on("data", chunk => text += chunk); process.stdin.on("end", () => resolve(text));
}));
const model = {id:"qualification", name:"Qualification", api:"openai-completions", provider:"openai",
  baseUrl:"https://qualification.invalid/v1", reasoning:false, input:["text"], contextWindow:32000,
  maxTokens:64, cost:{input:0,output:0,cacheRead:0,cacheWrite:0}};
const events = stream(model, {messages:[{role:"user", content:"qualification", timestamp:1}],
  tools:[{name:"qualification_echo",description:"Fixed echo",parameters:{type:"object",properties:{value:{type:"string"}}}}]},
  {apiKey:"offline-only", maxRetries:0, fetch:async () => new Response(input.raw,
    {status:200,headers:{"Content-Type":"text/event-stream"}})});
const result = await events.result();
process.stdout.write(JSON.stringify(result));
'''
    completed = subprocess.run([os.environ.get("PSKIT_TEST_NODE", "node"), "--input-type=module", "-e", source,
                                module.as_uri()], input=json.dumps({"raw": raw}), text=True,
                               capture_output=True, timeout=20, check=True)
    result = json.loads(completed.stdout)
    assert result["stopReason"] == "toolUse"
    call = next(block for block in result["content"] if block["type"] == "toolCall")
    assert call["id"] == "stable-call"
    assert call["arguments"] == {"value": "qualification"}
    assert call["name"] == ("qualification_" if split_name else "qualification_echo")
    assert ModelQualification._status("streaming_tools", document, "route", None) == ("failed" if split_name else "passed")
