from pathlib import Path


PROMPT = Path(__file__).parents[1] / "pi" / "system-prompt.md"


def test_downloadable_file_requests_require_real_tool_execution_and_artifact_panel():
    prompt = PROMPT.read_text(encoding="utf-8")

    assert "工作区文件工具的实际名称是 `read_file`、`write_file`" in prompt
    assert "`<write_file>`" in prompt and "不得用这些文本模拟工具调用" in prompt
    assert "只有写入工具返回成功且系统发布了对应 artifact 后，才能称文件可下载" in prompt
    assert "聊天中的附件卡片和右上角“产物”面板" in prompt
