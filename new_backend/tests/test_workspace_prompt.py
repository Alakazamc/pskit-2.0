from pathlib import Path


PROMPT = Path(__file__).parents[1] / "pi" / "system-prompt.md"


def test_downloadable_file_requests_require_real_tool_execution_and_artifact_panel():
    prompt = PROMPT.read_text(encoding="utf-8")

    assert "必须实际调用当前 Run 已注册的 `write` 工具" in prompt
    assert "`<write_file>`" in prompt and "伪工具标记" in prompt
    assert "没有收到写入工具成功结果时，明确说明文件未创建" in prompt
    assert "对话右上角的“产物”面板" in prompt
