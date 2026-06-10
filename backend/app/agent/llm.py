from __future__ import annotations

import httpx

from app.config import get_settings
from app.tools.catalog import openai_tool_schemas


class LlmUnavailable(RuntimeError):
    pass


def compact_message_content(value: object) -> str:
    if isinstance(value, str):
        return value
    return "" if value is None else str(value)


class OpenAICompatibleClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    def chat(self, messages: list[dict], tools: bool = True) -> dict:
        if not self.settings.llm_api_key:
            raise LlmUnavailable("LLM_API_KEY is not configured")
        payload: dict = {
            "model": self.settings.llm_model_id,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 1200,
        }
        if tools:
            payload["tools"] = openai_tool_schemas()
            payload["tool_choice"] = "auto"

        try:
            with httpx.Client(timeout=90) as client:
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


def default_system_prompt(retrieved_knowledge: list[dict]) -> str:
    lines = [
        "You are PSKit 2.0's bioinformatics agent.",
        "Use available tools for concrete molecular lookup, structure download, result reading, and analysis steps.",
        "You may call multiple tools across several turns when a user request needs lookup, download, analysis, and reporting.",
        "When the user asks for a report, call generate_session_report so the report is returned as a downloadable artifact.",
        "Do not invent files, model paths, or tool outputs.",
        "When using retrieved PSKit knowledge, mention the relevant source names briefly.",
    ]
    if retrieved_knowledge:
        lines.append("\nRetrieved PSKit Knowledge:")
        for index, item in enumerate(retrieved_knowledge[:5], start=1):
            source = item.get("source")
            heading = item.get("heading") or ""
            content = compact_message_content(item.get("content"))[:1200]
            lines.append(f"[{index}] {source} / {heading}\n{content}")
    return "\n".join(lines)
