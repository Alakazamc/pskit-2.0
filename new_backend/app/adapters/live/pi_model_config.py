"""Build Pi model settings from a trusted published profile, without endpoint overrides."""

from app.contracts.model_runtime import ModelRuntimeProfile, reasoning_wire_value


class PiModelConfigError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class PiModelConfigBuilder:
    def build(
        self, *, base_url: str, alias: str, provider_name: str = "model-gateway",
        supports_images: bool = False, reasoning_effort: str | None = None,
        profile: ModelRuntimeProfile | None = None,
    ) -> dict:
        """Keep legacy wire defaults; qualified profiles describe explicit Pi compat flags."""
        if profile is not None:
            if profile.alias != alias:
                raise PiModelConfigError("MODEL_PROFILE_MISMATCH")
            if profile.protocol != "chat_completions":
                raise PiModelConfigError("MODEL_PROTOCOL_UNSUPPORTED")
            if reasoning_effort and (
                reasoning_effort not in profile.reasoning_levels
                or profile.wire_policy.reasoning_mode == "omit"
            ):
                raise PiModelConfigError("MODEL_REASONING_UNAVAILABLE")
            supports_images = profile.supports_images
        model = {"id": alias, "input": ["text", "image"] if supports_images else ["text"]}
        if reasoning_effort:
            model["reasoning"] = True
            if reasoning_effort in {"xhigh", "max"}:
                model["thinkingLevelMap"] = {reasoning_effort: reasoning_wire_value(reasoning_effort)}
            elif reasoning_effort == "off":
                model["thinkingLevelMap"] = {"off": reasoning_wire_value("off")}
        if profile is not None:
            policy = profile.wire_policy
            model["reasoning"] = bool(profile.reasoning_levels)
            model["compat"] = {
                "maxTokensField": policy.chat_max_tokens_field,
                "supportsUsageInStreaming": policy.stream_usage_mode == "supported",
                "supportsDeveloperRole": policy.developer_role_mode == "supported",
                "supportsReasoningEffort": policy.reasoning_mode == "reasoning_effort",
            }
            if profile.context_window is not None:
                model["contextWindow"] = profile.context_window
            if profile.max_output_tokens is not None:
                model["maxTokens"] = profile.max_output_tokens
        return {"providers": {provider_name: {
            "baseUrl": f"{base_url.rstrip('/').removesuffix('/v1')}/v1",
            "api": "openai-completions",
            "apiKey": "$MODEL_GATEWAY_API_KEY",
            "models": [model],
        }}}
