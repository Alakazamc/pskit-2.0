"""Run in pinned LiteLLM 1.100.3 with --network none; synthetic SDK transports.

The regular driver tests assert these qualification-only parameters are emitted.
This check exercises their native SDK effect despite the gateway's global drop.
"""

import asyncio
import copy
import json
import unittest
from importlib.metadata import version
from itertools import product

import httpx
import litellm
from openai import AsyncOpenAI, OpenAI

TOOL = {"type": "function", "function": {"name": "qualification_echo", "parameters": {
    "type": "object", "properties": {"value": {"type": "string"}},
    "required": ["value"], "additionalProperties": False,
}}}


class StrictQualificationWireTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(version("litellm"), "1.100.3")
        self.previous_drop, self.previous_callbacks = litellm.drop_params, litellm.callbacks
        litellm.drop_params, litellm.callbacks = True, []

    def tearDown(self):
        litellm.drop_params, litellm.callbacks = self.previous_drop, self.previous_callbacks

    def test_request_false_alone_does_not_override_global_drop_in_pinned_version(self):
        optional = litellm.get_optional_params(model="gpt-4o-mini", custom_llm_provider="openai",
                                               reasoning_effort="high", drop_params=False)
        self.assertNotIn("reasoning_effort", optional)

    def test_strict_reasoning_and_all_choices_reach_native_sdk_wire(self):
        choices = ["auto", "required", {"type": "function", "function": {"name": "qualification_echo"}}]
        for provider, choice in product(("openai", "custom_openai"), choices):
            for effort in ("none", "high"):
                for stream in (False, True):
                    for asynchronous in (False, True):
                        with self.subTest(provider=provider, choice=choice, effort=effort, stream=stream, asynchronous=asynchronous):
                            seen = []
                            def respond(request, seen=seen, choice=choice, effort=effort, stream=stream):
                                body = json.loads(request.content)
                                seen.append(body)
                                self.assertEqual(body["tools"], [TOOL])
                                self.assertEqual(body["tool_choice"], choice)
                                self.assertEqual(body["reasoning_effort"], effort)
                                self.assertNotIn("drop_params", body)
                                self.assertNotIn("allowed_openai_params", body)
                                envelope = {"id": "qualification-wire", "created": 1, "model": "gpt-4o-mini"}
                                call = {"id": "stable-call", "type": "function", "function": {
                                    "name": "qualification_echo", "arguments": '{"value":"qualification"}',
                                }}
                                if not stream:
                                    return httpx.Response(200, json={**envelope, "object": "chat.completion",
                                        "choices": [{"index": 0, "message": {"role": "assistant", "content": None,
                                                                               "tool_calls": [call]},
                                                     "finish_reason": "tool_calls"}],
                                        "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}})
                                packets = [{**envelope, "object": "chat.completion.chunk", "choices": [{
                                    "index": 0, "delta": {"role": "assistant", "tool_calls": [{"index": 0, **call}]},
                                    "finish_reason": None,
                                }]}, {**envelope, "object": "chat.completion.chunk", "choices": [{
                                    "index": 0, "delta": {}, "finish_reason": "tool_calls",
                                }]}]
                                return httpx.Response(200, text="".join(f"data: {json.dumps(packet)}\n\n" for packet in packets)
                                    + "data: [DONE]\n\n", headers={"content-type": "text/event-stream"})
                            params = {"model": f"{provider}/gpt-4o-mini", "api_key": "offline-only",
                                "api_base": "https://qualification.invalid/v1", "max_tokens": 64,
                                "messages": [{"role": "user", "content": "Call qualification_echo."}],
                                "tools": [copy.deepcopy(TOOL)], "tool_choice": choice, "reasoning_effort": effort,
                                "drop_params": False, "allowed_openai_params": ["tools", "tool_choice", "reasoning_effort"],
                                "additional_drop_params": [], "stream": stream}
                            if asynchronous:
                                async def invoke(params=params, respond=respond, stream=stream):
                                    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                                        sdk = AsyncOpenAI(api_key="offline-only", max_retries=0, http_client=client)
                                        result = await litellm.acompletion(**params, client=sdk)
                                        if stream:
                                            chunks = [chunk async for chunk in result]
                                            self.assertTrue(any(chunk.choices[0].delta.tool_calls for chunk in chunks if chunk.choices))
                                        else:
                                            self.assertEqual(result.choices[0].message.tool_calls[0].id, "stable-call")
                                asyncio.run(invoke())
                            else:
                                with httpx.Client(transport=httpx.MockTransport(respond)) as client:
                                    sdk = OpenAI(api_key="offline-only", max_retries=0, http_client=client)
                                    result = litellm.completion(**params, client=sdk)
                                    if stream:
                                        self.assertTrue(any(chunk.choices[0].delta.tool_calls for chunk in result if chunk.choices))
                                    else:
                                        self.assertEqual(result.choices[0].message.tool_calls[0].id, "stable-call")
                            self.assertEqual(len(seen), 1)

    def test_provider_rejecting_preserved_reasoning_is_not_converted_to_success(self):
        seen = []
        def respond(request):
            seen.append(json.loads(request.content))
            return httpx.Response(400, json={"error": {"message": "Unsupported reasoning_effort", "type": "invalid_request_error"}})
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            sdk = OpenAI(api_key="offline-only", max_retries=0, http_client=client)
            with self.assertRaises(litellm.BadRequestError):
                litellm.completion(model="openai/gpt-4o-mini", api_key="offline-only", client=sdk,
                    messages=[{"role": "user", "content": "qualification"}], reasoning_effort="high",
                    drop_params=False, allowed_openai_params=["reasoning_effort"], additional_drop_params=[])
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["reasoning_effort"], "high")

    def test_router_deployment_defaults_cannot_remove_strict_request_parameters(self):
        seen = []
        def respond(request):
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "qualification-wire", "object": "chat.completion",
                "created": 1, "model": "gpt-4o-mini", "choices": [{"index": 0,
                    "message": {"role": "assistant", "content": "qualification"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}})
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            sdk = OpenAI(api_key="offline-only", max_retries=0, http_client=client)
            router = litellm.Router(model_list=[{"model_name": "qualification-alias", "litellm_params": {
                "model": "openai/gpt-4o-mini", "api_base": "https://qualification.invalid/v1",
                "api_key": "offline-only", "drop_params": True, "additional_drop_params": ["reasoning_effort"],
            }, "model_info": {"id": "qualification-deployment"}}], num_retries=0)
            router.completion(model="qualification-alias", messages=[{"role": "user", "content": "qualification"}],
                reasoning_effort="high", drop_params=False, allowed_openai_params=["reasoning_effort"],
                additional_drop_params=[], client=sdk)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["reasoning_effort"], "high")


if __name__ == "__main__":
    unittest.main()
