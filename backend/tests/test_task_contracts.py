from uuid import uuid4

import pytest
from sqlalchemy import event

from app.api.tasks import list_tasks
from app.db.models import Artifact, Task, TaskRetry, User
from app.db.session import SessionLocal, engine
from app.tasks.validation import validate_task_input
from app.tools.mcp_adapters import MCPAdapterError, build_pepccd_mcp_payload


@pytest.mark.parametrize("invalid", [
    {"num_peptides": None}, {"num_peptides": 1.5}, {"num_peptides": []},
    {"temperature": None}, {"protein_sequence": "not-a-protein!"},
    {"iteration": -1}, {"iteration": {}},
])
def test_invalid_pepccd_input_is_a_validation_error(invalid):
    payload = {"research_run_id": str(uuid4()), "protein_sequence": "ACDE", **invalid}
    with pytest.raises(ValueError, match="Invalid task input fields"):
        validate_task_input("generate_pepccd_candidates", payload)
    if "iteration" not in invalid:
        with pytest.raises(MCPAdapterError, match="Invalid PepCCD input fields"):
            build_pepccd_mcp_payload(payload)


def test_pepccd_queue_and_mcp_share_normalization():
    queued = validate_task_input("generate_pepccd_candidates", {
        "research_run_id": str(uuid4()), "protein_sequence": " aCd- e\n",
    })
    remote = build_pepccd_mcp_payload(queued)
    assert remote["protein_sequence"] == "ACDE"
    assert remote["num_peptides"] == 10
    assert "research_run_id" not in remote


def test_task_list_batches_related_data_without_leaking_other_users():
    with SessionLocal() as db:
        owner = User(username="task-list-owner", password_hash="unused", role="user")
        other = User(username="task-list-other", password_hash="unused", role="user")
        db.add_all([owner, other])
        db.flush()
        tasks = [Task(user_id=owner.id, task_type="predict_interaction", status="queued")
                 for _ in range(20)]
        db.add_all(tasks)
        db.add(Task(user_id=other.id, task_type="predict_interaction", status="queued"))
        db.flush()
        db.add(TaskRetry(parent_task_id=tasks[0].id, child_task_id=tasks[1].id,
                         client_retry_id=uuid4(), reason="manual"))
        db.add_all([
            Artifact(user_id=owner.id, task_id=tasks[0].id, kind="result", filename="own.json",
                     storage_backend="local", object_key="own.json"),
            Artifact(user_id=other.id, task_id=tasks[0].id, kind="result", filename="other.json",
                     storage_backend="local", object_key="other.json"),
        ])
        db.commit()
        db.refresh(owner)
        queries = []

        def count_query(_conn, _cursor, statement, _params, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                queries.append(statement)

        event.listen(engine, "before_cursor_execute", count_query)
        try:
            result = list_tasks(limit=100, offset=0, db=db, user=owner)
        finally:
            event.remove(engine, "before_cursor_execute", count_query)
        assert len(result) == 20
        assert len(queries) == 3
        parent = next(item for item in result if item.id == str(tasks[0].id))
        assert parent.retry_task_id == str(tasks[1].id)
        assert [artifact.filename for artifact in parent.artifacts] == ["own.json"]
        assert parent.input is None and parent.output is None
