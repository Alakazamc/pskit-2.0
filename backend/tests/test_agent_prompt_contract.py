from app.agent.llm import default_system_prompt


def test_bioinformatics_prompt_keeps_predictions_and_evidence_distinct():
    prompt = default_system_prompt([])

    assert "computational predictions" in prompt
    assert "experimentally validated findings" in prompt
    assert "task ID" in prompt
    assert "artifact ID" in prompt
    assert "do not promise automatic analysis" in prompt
