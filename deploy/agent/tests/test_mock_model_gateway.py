"""The local model stub supports a controlled delay for proxy SSE acceptance."""

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).with_name("mock_model_gateway.py")
spec = importlib.util.spec_from_file_location("mock_model_gateway", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_only_explicit_user_probe_selects_delayed_reply():
    assert module.response_kind({"messages": [
        {"role": "user", "content": "SSE_DELAY_PROBE: Say hello briefly."},
    ]}) == "delayed"
    assert module.response_kind({"messages": [
        {"role": "user", "content": "Say hello briefly."},
    ]}) == "normal"
    assert module.response_kind({"messages": [
        {"role": "system", "content": "SSE_DELAY_PROBE"},
        {"role": "user", "content": "Say hello briefly."},
    ]}) == "normal"
