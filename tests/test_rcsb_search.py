import pytest
from app.tools.external import ToolExecutionError, build_rcsb_full_text_payload


def test_rcsb_search_uses_full_text_service() -> None:
    payload = build_rcsb_full_text_payload("TATA binding protein")

    assert payload["query"] == {
        "type": "terminal",
        "service": "full_text",
        "parameters": {"value": "TATA binding protein"},
    }
    assert payload["request_options"]["paginate"] == {"start": 0, "rows": 10}
    assert "attribute" not in payload["query"]["parameters"]


def test_rcsb_search_rejects_empty_query() -> None:
    with pytest.raises(ToolExecutionError, match="query must not be empty"):
        build_rcsb_full_text_payload("   ")
