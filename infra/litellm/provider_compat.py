"""Narrow wire compatibility for the configured PinCC Anthropic upstream.

LiteLLM 1.100.3 invokes log_pre_api_call synchronously with the native body,
after Anthropic conversion and before HTTP serialization. The integration test
checks this boundary for sync/async and streaming/non-streaming calls. Recheck
that contract before upgrading LiteLLM; incoming OpenAI tools are too early.
"""

from importlib.metadata import version
from urllib.parse import urlsplit

from litellm.integrations.custom_logger import CustomLogger


def normalize_pincc_tools(api_base: object, body: object) -> bool:
    """Remove only PinCC's incompatible optional discriminator, in place.

    Copy the tools and changed entries, so caller-owned tool definitions stay
    intact. Never interpret assistant text or alter tool names/schemas/results.
    """
    if not isinstance(api_base, str) or not isinstance(body, dict):
        return False
    try:
        url = urlsplit(api_base)
        matches = (
            url.scheme == "https"
            and url.hostname == "v2.pincc.ai"
            and url.port in (None, 443)
            and url.path.rstrip("/") == "/v1/messages"
        )
    except ValueError:
        return False
    if not matches or not isinstance(body.get("tools"), list):
        return False
    changed = False
    tools = []
    for tool in body["tools"]:
        if (isinstance(tool, dict) and tool.get("type") == "custom"
                and isinstance(tool.get("name"), str)
                and isinstance(tool.get("input_schema"), dict)):
            tool = {key: value for key, value in tool.items() if key != "type"}
            changed = True
        tools.append(tool)
    if changed:
        body["tools"] = tools
    return changed


class PinccToolCompatibility(CustomLogger):
    def __init__(self):
        super().__init__()
        if version("litellm") != "1.100.3":
            raise RuntimeError("Requalify provider_compat against the pinned LiteLLM version")

    def log_pre_api_call(self, model, messages, kwargs):
        params = kwargs.get("litellm_params") or {}
        if params.get("custom_llm_provider") != "anthropic":
            return
        args = kwargs.get("additional_args") or {}
        normalize_pincc_tools(args.get("api_base"), args.get("complete_input_dict"))


pincc_tool_compatibility = PinccToolCompatibility()
