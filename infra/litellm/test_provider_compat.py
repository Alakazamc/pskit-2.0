"""Offline wire checks; run with the exact gateway image, no provider keys."""

import asyncio
import copy
import json
import unittest
from unittest.mock import patch

import httpx
import litellm
from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler, HTTPHandler
from openai import AsyncOpenAI, OpenAI
from provider_compat import (
    PinccToolCompatibility,
    normalize_pincc_tools,
    pincc_tool_compatibility,
)


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.callbacks = litellm.callbacks
        self.drop_params = litellm.drop_params

    def tearDown(self):
        litellm.callbacks = self.callbacks
        litellm.drop_params = self.drop_params

    def test_version_mismatch_requires_requalification(self):
        with patch("provider_compat.version", return_value="1.100.4"):
            with self.assertRaisesRegex(RuntimeError, "Requalify"):
                PinccToolCompatibility()

    def test_only_matching_native_custom_tools_change(self):
        tools = [
            {"type": "custom", "name": "write_file", "input_schema": {"type": "object"},
             "description": "write", "cache_control": {"type": "ephemeral"}},
            {"type": "web_search_20250305", "name": "web_search"},
            {"type": "function", "function": {"name": "write_file"}},
        ]
        original = copy.deepcopy(tools)
        body = {"tools": tools, "tool_choice": {"type": "auto"}, "messages": []}
        self.assertTrue(normalize_pincc_tools("https://v2.pincc.ai/v1/messages", body))
        self.assertEqual(tools, original)
        self.assertEqual(body["tools"][0], {k: v for k, v in original[0].items() if k != "type"})
        self.assertEqual(body["tools"][1:], original[1:])
        self.assertEqual(body["tool_choice"], {"type": "auto"})
        self.assertFalse(normalize_pincc_tools("https://v2.pincc.ai/v1/messages", body))

    def test_actual_openai_callback_preserves_wire_all_modes(self):
        """Exercise the SDK transport and real callback, without provider access."""
        litellm.callbacks = [pincc_tool_compatibility]
        tool = {"type": "function", "function": {
            "name": "diagnostic_echo", "description": "Echo a harmless value",
            "parameters": {"type": "object", "properties": {"value": {"type": "string"}},
                           "required": ["value"]},
        }}
        for stream in (False, True):
            for asynchronous in (False, True):
                with self.subTest(stream=stream, asynchronous=asynchronous):
                    seen = []

                    def respond(request):
                        self.assertEqual(request.url.host, "api.openai.com")
                        self.assertEqual(request.url.path, "/v1/chat/completions")
                        body = json.loads(request.content)
                        seen.append(body)
                        self.assertEqual(body["tools"], [tool])
                        self.assertEqual(body["tool_choice"], {
                            "type": "function", "function": {"name": "diagnostic_echo"},
                        })
                        call = {"id": "call_wire", "type": "function", "function": {
                            "name": "diagnostic_echo", "arguments": '{"value":"pong"}',
                        }}
                        envelope = {"id": "chatcmpl-wire", "created": 1, "model": "gpt-4o-mini"}
                        if not stream:
                            return httpx.Response(200, json={**envelope, "object": "chat.completion",
                                "choices": [{"index": 0, "message": {"role": "assistant",
                                             "content": None, "tool_calls": [call]},
                                             "finish_reason": "tool_calls"}],
                                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
                        packets = [{**envelope, "object": "chat.completion.chunk", "choices": [
                            {"index": 0, "delta": {"role": "assistant", "tool_calls": [
                                {"index": 0, **call}]}, "finish_reason": None}]},
                            {**envelope, "object": "chat.completion.chunk", "choices": [
                                {"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}]
                        data = "".join(f"data: {json.dumps(packet)}\n\n" for packet in packets)
                        return httpx.Response(200, text=data + "data: [DONE]\n\n",
                                              headers={"content-type": "text/event-stream"})

                    params = dict(model="openai/gpt-4o-mini", api_key="offline-only",
                                  api_base="https://api.openai.com/v1",
                                  messages=[{"role": "user", "content": "Echo pong"}],
                                  tools=[copy.deepcopy(tool)], stream=stream, max_tokens=64,
                                  tool_choice={"type": "function", "function": {"name": "diagnostic_echo"}})
                    with patch.object(pincc_tool_compatibility, "log_pre_api_call",
                                      wraps=pincc_tool_compatibility.log_pre_api_call) as callback:
                        if asynchronous:
                            async def invoke():
                                async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                                    sdk = AsyncOpenAI(api_key="offline-only", max_retries=0,
                                                      http_client=client)
                                    result = await litellm.acompletion(**params, client=sdk)
                                    if stream:
                                        chunks = [chunk async for chunk in result]
                                        self.assertTrue(any(c.choices[0].delta.tool_calls for c in chunks if c.choices))
                                    else:
                                        self.assertEqual(result.choices[0].message.tool_calls[0].function.name,
                                                         "diagnostic_echo")
                            asyncio.run(invoke())
                        else:
                            with httpx.Client(transport=httpx.MockTransport(respond)) as client:
                                sdk = OpenAI(api_key="offline-only", max_retries=0,
                                             http_client=client)
                                result = litellm.completion(**params, client=sdk)
                                if stream:
                                    self.assertTrue(any(c.choices[0].delta.tool_calls for c in result if c.choices))
                                else:
                                    self.assertEqual(result.choices[0].message.tool_calls[0].function.name,
                                                     "diagnostic_echo")
                        self.assertGreater(callback.call_count, 0)
                        callback_kwargs = (callback.call_args.kwargs.get("kwargs")
                                           or callback.call_args.args[2])
                        self.assertEqual(callback_kwargs["litellm_params"]["custom_llm_provider"], "openai")
                    self.assertEqual(len(seen), 1)

    def test_unrelated_endpoints_and_invalid_bodies_unchanged(self):
        for url in ["https://api.anthropic.com/v1/messages", "https://v2.pincc.ai.evil.test/v1/messages",
                    "http://v2.pincc.ai/v1/messages", "https://v2.pincc.ai/v1/chat/completions",
                    "https://v2.pincc.ai:8443/v1/messages", "https://[invalid", None]:
            body = {"tools": [{"type": "custom", "name": "write_file", "input_schema": {}}]}
            before = copy.deepcopy(body)
            self.assertFalse(normalize_pincc_tools(url, body))
            self.assertEqual(body, before)
        for body in [None, [], {"tools": None}, {"tools": "invalid"}, {"tools": [{}]}]:
            self.assertFalse(normalize_pincc_tools("https://v2.pincc.ai/v1/messages", body))

    def test_actual_litellm_wire_all_modes(self):
        litellm.callbacks = [pincc_tool_compatibility]
        litellm.drop_params = True
        tools = [{"type": "function", "function": {
            "name": "diagnostic_echo", "description": "Echo a harmless value",
            "parameters": {"type": "object", "properties": {"value": {"type": "string"}},
                           "required": ["value"]},
        }}]
        original = copy.deepcopy(tools)
        for host in ["v2.pincc.ai", "api.anthropic.com"]:
            for stream in [False, True]:
                for asynchronous in [False, True]:
                    with self.subTest(host=host, stream=stream, asynchronous=asynchronous):
                        seen = []

                        def respond(request):
                            body = json.loads(request.content)
                            seen.append(body)
                            self.assertEqual(body["tools"][0]["name"], "diagnostic_echo")
                            self.assertEqual(body["tools"][0]["input_schema"], tools[0]["function"]["parameters"])
                            self.assertEqual(body["tool_choice"], {"type": "tool", "name": "diagnostic_echo"})
                            self.assertEqual("type" in body["tools"][0], host != "v2.pincc.ai")
                            message = {"id": "msg_wire", "type": "message", "role": "assistant",
                                       "model": "claude-sonnet-4-5-20250929", "content": [],
                                       "stop_reason": None, "stop_sequence": None,
                                       "usage": {"input_tokens": 1, "output_tokens": 1}}
                            tool = {"type": "tool_use", "id": "tool_wire", "name": "diagnostic_echo",
                                    "input": {"value": "pong"}}
                            if not stream:
                                return httpx.Response(200, json={**message, "content": [tool], "stop_reason": "tool_use"})
                            events = [
                                {"type": "message_start", "message": message},
                                {"type": "content_block_start", "index": 0, "content_block": {**tool, "input": {}}},
                                {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "input_json_delta", "partial_json": '{"value":"pong"}'}},
                                {"type": "content_block_stop", "index": 0},
                                {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                                 "usage": {"output_tokens": 1}},
                                {"type": "message_stop"},
                            ]
                            data = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
                            return httpx.Response(200, text=data, headers={"content-type": "text/event-stream"})

                        params = dict(model="anthropic/claude-sonnet-4-5-20250929", api_key="offline-only",
                                      api_base=f"https://{host}", messages=[{"role": "user", "content": "Echo pong"}],
                                      tools=copy.deepcopy(tools), max_tokens=64, stream=stream,
                                      tool_choice={"type": "function", "function": {"name": "diagnostic_echo"}})
                        if asynchronous:
                            async def invoke():
                                handler = AsyncHTTPHandler()
                                await handler.client.aclose()
                                async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                                    handler.client = client
                                    result = await litellm.acompletion(**params, client=handler)
                                    if stream:
                                        chunks = [chunk async for chunk in result]
                                        self.assertTrue(any(c.choices[0].delta.tool_calls for c in chunks if c.choices))
                                    else:
                                        self.assertEqual(result.choices[0].message.tool_calls[0].function.name, "diagnostic_echo")
                            asyncio.run(invoke())
                        else:
                            with httpx.Client(transport=httpx.MockTransport(respond)) as client:
                                result = litellm.completion(**params, client=HTTPHandler(client=client))
                                if stream:
                                    self.assertTrue(any(c.choices[0].delta.tool_calls for c in result if c.choices))
                                else:
                                    self.assertEqual(result.choices[0].message.tool_calls[0].function.name, "diagnostic_echo")
                        self.assertEqual(len(seen), 1)
        self.assertEqual(tools, original)


if __name__ == "__main__":
    unittest.main()
