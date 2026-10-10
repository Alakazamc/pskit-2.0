"""Pi's existing Chat Completions config and bounded published wire settings."""

import pytest

from app.adapters.live.pi_model_config import PiModelConfigBuilder, PiModelConfigError
from app.contracts.model_runtime import ModelRuntimeProfile, ModelWirePolicy


def build(**kwargs):
    return PiModelConfigBuilder().build(
        base_url="http://internal.invalid/internal/model/v1", alias="lab-alias",
        provider_name="model-gateway", **kwargs,
    )["providers"]["model-gateway"]


def test_legacy_config_is_identical_and_key_is_an_environment_reference():
    assert build() == {
        "baseUrl": "http://internal.invalid/internal/model/v1", "api": "openai-completions",
        "apiKey": "$MODEL_GATEWAY_API_KEY", "models": [{"id": "lab-alias", "input": ["text"]}],
    }


@pytest.mark.parametrize("level", ["off", "high", "xhigh", "max"])
def test_legacy_reasoning_mapping_is_preserved(level):
    model = build(reasoning_effort=level, supports_images=True)["models"][0]
    assert model["reasoning"] is True and model["input"] == ["text", "image"]
    if level in {"max", "xhigh"}:
        assert model["thinkingLevelMap"] == {level: level}
    if level == "off":
        assert model["thinkingLevelMap"] == {"off": "none"}


def test_published_profile_only_emits_verified_closed_wire_settings():
    profile = ModelRuntimeProfile(
        alias="lab-alias", revision=2, supports_images=False,
        reasoning_levels=["off", "high"], context_window=32000, max_output_tokens=2048,
        wire_policy=ModelWirePolicy(
            chat_max_tokens_field="max_completion_tokens", stream_usage_mode="not_supported",
            developer_role_mode="system_fallback", reasoning_mode="reasoning_effort",
        ),
    )
    model = build(profile=profile, supports_images=True, reasoning_effort="high")["models"][0]
    assert model["input"] == ["text"]
    assert model["contextWindow"] == 32000 and model["maxTokens"] == 2048
    assert model["compat"] == {
        "maxTokensField": "max_completion_tokens", "supportsUsageInStreaming": False,
        "supportsDeveloperRole": False, "supportsReasoningEffort": True,
    }


def test_unknown_wire_capabilities_do_not_enable_optional_fields():
    model = build(profile=ModelRuntimeProfile(alias="lab-alias", revision=1))["models"][0]
    assert model["compat"]["supportsUsageInStreaming"] is False
    assert model["compat"]["supportsDeveloperRole"] is False
    assert model["compat"]["supportsReasoningEffort"] is False


@pytest.mark.parametrize("profile,level,code", [
    (ModelRuntimeProfile(alias="lab-alias", revision=1, protocol="responses"), None,
     "MODEL_PROTOCOL_UNSUPPORTED"),
    (ModelRuntimeProfile(alias="different-alias", revision=1), None, "MODEL_PROFILE_MISMATCH"),
    (ModelRuntimeProfile(alias="lab-alias", revision=1), "high", "MODEL_REASONING_UNAVAILABLE"),
    (ModelRuntimeProfile(alias="lab-alias", revision=1, reasoning_levels=["high"]), "high",
     "MODEL_REASONING_UNAVAILABLE"),
])
def test_incompatible_profile_fails_explicitly_before_starting_pi(profile, level, code):
    with pytest.raises(PiModelConfigError) as caught:
        build(profile=profile, reasoning_effort=level)
    assert caught.value.code == code
