from datetime import timedelta
from uuid import uuid4

import pytest

from app.agent.llm import default_system_prompt
from app.artifacts.service import register_local_artifact, task_artifact_dir
from app.db.models import Task, User, now_utc
from app.db.session import SessionLocal
from app.tools.catalog import openai_tool_schemas
from app.tools.external import ToolExecutionError
from app.tools.runner import ToolContext, execute_tool


def test_task_artifact_tool_schema_and_prompt():
    schemas = openai_tool_schemas({"list_task_artifacts"})
    assert len(schemas) == 1
    parameters = schemas[0]["function"]["parameters"]
    assert parameters["required"] == ["task_id"]
    assert parameters["properties"]["offset"]["minimum"] == 0
    assert parameters["properties"]["limit"]["maximum"] == 100
    assert parameters["properties"]["limit"]["default"] == 8
    prompt = default_system_prompt([])
    assert "list_task_artifacts" in prompt
    assert "follow next_offset" in prompt
    assert "retry the same offset with a smaller limit" in prompt


def test_list_task_artifacts_reads_old_owned_task_without_leaking_paths_or_errors():
    db = SessionLocal()
    try:
        owner = User(username=f"artifact_owner_{uuid4().hex[:8]}", password_hash="unused")
        other = User(username=f"artifact_other_{uuid4().hex[:8]}", password_hash="unused")
        db.add_all([owner, other])
        db.flush()
        old_task = Task(
            user_id=owner.id,
            task_type="predict_interaction",
            status="succeeded",
            updated_at=now_utc() - timedelta(days=1),
        )
        failed_task = Task(
            user_id=owner.id,
            task_type="search_structure_homologs",
            status="failed",
            error_type="TaskWorkerError",
            error_message="private diagnostic at /secret/host/path",
        )
        db.add_all([old_task, failed_task])
        db.flush()
        output_dir = task_artifact_dir(owner.id, old_task.id)
        registered_artifacts = []
        for index in range(23):
            path = output_dir / f"old_result_{index:02d}.json"
            path.write_text('{"result": "test"}', encoding="utf-8")
            artifact = register_local_artifact(
                db, owner, None, old_task.id, path, "json", "application/json", commit=False
            )
            artifact.created_at = now_utc() + timedelta(seconds=index)
            registered_artifacts.append(artifact)
        db.add_all(
            [
                Task(user_id=owner.id, task_type="predict_interaction", status="succeeded")
                for _ in range(6)
            ]
        )
        db.commit()

        owner_context = ToolContext(db, owner, None, "old-task-lookup")
        result = execute_tool(
            "list_task_artifacts", {"task_id": str(old_task.id)}, owner_context
        )
        assert result["status"] == "succeeded"
        assert result["error_type"] is None
        assert result["offset"] == 0
        assert result["total_count"] == 23
        assert len(result["artifacts"]) == 8
        assert result["next_offset"] == 8
        pages = [result]
        while pages[-1]["next_offset"] is not None:
            pages.append(
                execute_tool(
                    "list_task_artifacts",
                    {"task_id": str(old_task.id), "offset": pages[-1]["next_offset"]},
                    owner_context,
                )
            )
        assert [len(page["artifacts"]) for page in pages] == [8, 8, 7]
        returned = [item for page in pages for item in page["artifacts"]]
        assert [item["artifact_id"] for item in returned] == [
            str(item.id) for item in registered_artifacts
        ]
        assert [item["filename"] for item in returned] == [
            f"old_result_{index:02d}.json" for index in range(23)
        ]
        assert all(item["kind"] == "json" for item in returned)
        assert str(output_dir) not in str(pages)

        failure = execute_tool(
            "list_task_artifacts", {"task_id": str(failed_task.id)}, owner_context
        )
        assert failure["status"] == "failed"
        assert failure["error_type"] == "TaskWorkerError"
        assert failure["artifacts"] == []
        assert "private diagnostic" not in str(failure)

        other_context = ToolContext(db, other, None, "foreign-task-lookup")
        with pytest.raises(ToolExecutionError, match="Task not found"):
            execute_tool("list_task_artifacts", {"task_id": str(old_task.id)}, other_context)
        with pytest.raises(ToolExecutionError, match="Task not found"):
            execute_tool("list_task_artifacts", {"task_id": str(uuid4())}, other_context)
        with pytest.raises(ToolExecutionError, match="task_id must be a valid UUID"):
            execute_tool("list_task_artifacts", {"task_id": "not-a-uuid"}, owner_context)
        with pytest.raises(ToolExecutionError, match="offset must be a non-negative integer"):
            execute_tool(
                "list_task_artifacts", {"task_id": str(old_task.id), "offset": -1}, owner_context
            )
        with pytest.raises(ToolExecutionError, match="limit must be an integer from 1 to 100"):
            execute_tool(
                "list_task_artifacts", {"task_id": str(old_task.id), "limit": 101}, owner_context
            )
    finally:
        db.close()
