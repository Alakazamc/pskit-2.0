from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import get_settings
from app.tools.catalog import openai_tool_schemas


class LlmUnavailable(RuntimeError):
    pass


def compact_message_content(value: object) -> str:
    if isinstance(value, str):
        return value
    return "" if value is None else str(value)


def parse_stream_line(line: str) -> tuple[bool, str | None]:
    if not line.startswith("data:"):
        return False, None
    raw = line.removeprefix("data:").strip()
    if not raw:
        return False, None
    if raw == "[DONE]":
        return True, None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return False, None
    choices = data.get("choices") or []
    if not choices:
        return False, None
    delta = choices[0].get("delta") or {}
    content = delta.get("content")
    return False, content if isinstance(content, str) and content else None


class OpenAICompatibleClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    def chat(
        self,
        messages: list[dict],
        tools: bool = True,
        tool_schemas: list[dict] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        thinking: bool | None = None,
    ) -> dict:
        if not self.settings.llm_api_key:
            raise LlmUnavailable("LLM_API_KEY is not configured")
        payload: dict = {
            "model": self.settings.llm_model_id,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 1200,
        }
        if tools:
            payload["tools"] = tool_schemas if tool_schemas is not None else openai_tool_schemas()
            payload["tool_choice"] = tool_choice if tool_choice is not None else "auto"
        provider_hint = (
            f"{self.settings.llm_model_id} {self.settings.chat_completions_url}".lower()
        )
        # wzf：DeepSeek V4 的思考模式与指定函数 tool_choice 不兼容；
        # 仅对 DeepSeek 请求发送该扩展字段，避免破坏其他 OpenAI 兼容服务。
        if thinking is not None and "deepseek" in provider_hint:
            payload["thinking"] = {
                "type": "enabled" if thinking else "disabled",
            }

        try:
            with httpx.Client(
                timeout=getattr(self.settings, "llm_request_timeout_seconds", 90)
            ) as client:
                response = client.post(
                    self.settings.chat_completions_url,
                    headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
                    json=payload,
                )
        except httpx.RequestError as exc:
            raise LlmUnavailable(
                "LLM API unreachable. "
                f"url={self.settings.chat_completions_url}; "
                f"reason={exc}. "
                "Check server outbound network, campus gateway, or configure an accessible proxy endpoint."
            ) from exc
        if response.status_code >= 400:
            raise LlmUnavailable(f"LLM API error {response.status_code}: {response.text[:500]}")
        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise LlmUnavailable("LLM API returned no choices")
        return choices[0].get("message") or {}

    def stream_chat_sync(self, messages: list[dict]):
        if not self.settings.llm_api_key:
            raise LlmUnavailable("LLM_API_KEY is not configured")

        payload = {
            "model": self.settings.llm_model_id,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 1200,
            "stream": True,
        }
        timeout = httpx.Timeout(
            connect=30,
            read=getattr(self.settings, "llm_stream_read_timeout_seconds", 120),
            write=30,
            pool=30,
        )

        try:
            with httpx.Client(timeout=timeout) as client:
                with client.stream(
                    "POST",
                    self.settings.chat_completions_url,
                    headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
                    json=payload,
                ) as response:
                    if response.status_code >= 400:
                        body = response.read().decode("utf-8", errors="replace")
                        raise LlmUnavailable(
                            f"LLM API error {response.status_code}: {body[:500]}"
                        )
                    for line in response.iter_lines():
                        done, content = parse_stream_line(line)
                        if content:
                            yield content
                        if done:
                            break
        except LlmUnavailable:
            raise
        except httpx.RequestError as exc:
            raise LlmUnavailable(
                "LLM streaming API unreachable. "
                f"url={self.settings.chat_completions_url}; "
                f"reason={exc}. "
                "Check server outbound network, campus gateway, or configure an accessible proxy endpoint."
            ) from exc

    async def stream_chat(self, messages: list[dict]):
        if not self.settings.llm_api_key:
            raise LlmUnavailable("LLM_API_KEY is not configured")

        payload = {
            "model": self.settings.llm_model_id,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 1200,
            "stream": True,
        }
        timeout = httpx.Timeout(
            connect=30,
            read=getattr(self.settings, "llm_stream_read_timeout_seconds", 120),
            write=30,
            pool=30,
        )

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    self.settings.chat_completions_url,
                    headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
                    json=payload,
                ) as response:
                    if response.status_code >= 400:
                        body = (await response.aread()).decode("utf-8", errors="replace")
                        raise LlmUnavailable(
                            f"LLM API error {response.status_code}: {body[:500]}"
                        )

                    async for line in response.aiter_lines():
                        done, content = parse_stream_line(line)
                        if content:
                            yield content
                        if done:
                            break
        except LlmUnavailable:
            raise
        except httpx.RequestError as exc:
            raise LlmUnavailable(
                "LLM streaming API unreachable. "
                f"url={self.settings.chat_completions_url}; "
                f"reason={exc}. "
                "Check server outbound network, campus gateway, or configure an accessible proxy endpoint."
            ) from exc


def default_system_prompt(retrieved_knowledge: list[dict]) -> str:
    lines = [
        "You are PSKit 2.0's bioinformatics agent.",
        "Use available tools for concrete molecular lookup, structure download, result reading, and analysis steps.",
        "You may call multiple tools across several turns when a user request needs lookup, download, analysis, and reporting.",
        "Treat persisted execution bookkeeping as internal server state, never as a user-facing workflow or prerequisite.",
        "The server resolves attribution from the active agent session. Never ask the user to create, select, bind, or provide an internal execution record or identifier.",
        "For homology analysis, sequence and structure searches are independent required branches; for candidate work, CORAL RNA and PepCCD peptide are independent required branches.",
        "For candidate generation, call generate_coral_candidates or generate_pepccd_candidates as soon as the required scientific input is known; queuing them does not require a user-selected execution record or scoring configuration.",
        "Long-running tools only enqueue work. After one or more long tasks are queued, stop the current turn. The client displays task status; do not promise automatic analysis when they finish. On a later user request, inspect their completed tasks and registered artifacts before answering.",
        "Use persisted task facts to avoid duplicate work, but do not ask the user to manipulate stages or internal records. Only actual tool validation or execution errors block a step.",
        "Avoid repeating an identical tool call when it is unnecessary, but you may call the same tool again when the user requests it, the prior result is insufficient, or a retry is needed.",
        "For artifact-backed tools such as predict_binding_sites, reuse the registered artifact_id returned by download_pdb_file or a previous structure tool. Prefer artifact_id over pdb_path, and never invent, reconstruct, or copy a host filesystem path.",
        "If you have a task_id for an older completed or failed task but no artifact list, call list_task_artifacts with that exact task_id. It returns only owned task status, error type, and registered artifact IDs. Use the default small page or limit=8, then follow next_offset with the same task_id until it is null. If a page is truncated, retry the same offset with a smaller limit before advancing; never guess omitted artifact IDs or derive one from a task_id or filename.",
        "For read_result_file, use an exact server-provided artifact_id. A task_id is not an artifact_id, and a filename or download URL is not a file_path. When completed-task artifacts are listed in system context, read the relevant artifact directly instead of generating a report to rediscover it; prefer candidates.json for candidate content unless raw output is explicitly requested.",
        "When a persisted execution context is attached, use the report tool appropriate to that context; otherwise use generate_session_report.",
        "Candidate scoring is optional downstream work. Do not invent weights, directions, or thresholds. If ranking is not explicitly requested or no configuration is available, defer ranking without blocking upstream lookup, structure analysis, or candidate generation.",
        "Do not invent files, model paths, or tool outputs.",
        "Separate database facts, computational predictions, and experimentally validated findings. Never present an in-silico score or structure as experimental proof of binding.",
        "When interpreting scientific results, retain the target accession and chain, sequence type, tool/model name, relevant parameters, task ID, and artifact ID when available. If evidence is absent or a tool failed, say so explicitly rather than filling the gap with a plausible result.",
        "When using retrieved PSKit knowledge, mention the relevant source names briefly.",
    ]
    if retrieved_knowledge:
        lines.append("\nRetrieved PSKit Knowledge:")
        for index, item in enumerate(retrieved_knowledge[:5], start=1):
            source = item.get("source")
            heading = item.get("heading") or ""
            content = compact_message_content(item.get("content"))[:1200]
            lines.append(f"[{index}] {source} / {heading}\n{content}")
    lines.append(
        "\nFinal product contract: keep the conversation focused on scientific inputs and outputs. "
        "Never turn internal persistence, task linkage, or ranking configuration into a prerequisite for upstream work."
    )
    return "\n".join(lines)
