import asyncio

import httpx
import pytest

from app.config import Settings
from app.contracts.conversation import MessageRequest
from app.domain.persistent_conversation import PersistentConversationStore
from app.main import create_app


def test_pi_claims_obey_global_and_per_user_limits_across_store_instances(tmp_path):
    path = str(tmp_path / "agent.sqlite3")
    first = PersistentConversationStore(path)
    second = PersistentConversationStore(path)
    alice, bob = "alice", "bob"
    alice_project = first.project_for(alice).id
    bob_project = first.project_for(bob).id
    alice_sessions = [first.create_session(alice, alice_project, f"A {i}").id for i in range(3)]
    bob_sessions = [first.create_session(bob, bob_project, f"B {i}").id for i in range(2)]
    alice_runs = [first.send_message(alice, session, MessageRequest(content="research")).run_id
                  for session in alice_sessions]
    bob_runs = [first.send_message(bob, session, MessageRequest(content="research")).run_id
                for session in bob_sessions]

    assert first.claim_initial_run(alice_runs[0], "worker-a", max_active=3, max_user_active=2)
    assert second.claim_initial_run(alice_runs[1], "worker-b", max_active=3, max_user_active=2)
    assert not first.claim_initial_run(alice_runs[2], "worker-a", max_active=3, max_user_active=2)
    assert second.claim_initial_run(bob_runs[0], "worker-b", max_active=3, max_user_active=2)
    assert not first.claim_initial_run(bob_runs[1], "worker-a", max_active=3, max_user_active=2)
    assert first.run_status_for(alice, alice_runs[2]).status == "queued"
    first.set_run_status(alice_runs[0], "completed")
    assert second.claim_initial_run(alice_runs[2], "worker-b", max_active=3, max_user_active=2)


@pytest.mark.asyncio
async def test_pi_scheduler_queues_runs_until_a_slot_is_released(tmp_path):
    class BlockingPi:
        def __init__(self):
            self.started: list[str] = []
            self.release = asyncio.Event()

        async def prompt(self, session_id, message, on_event, **kwargs):
            self.started.append(session_id)
            await self.release.wait()
            return {"session_file": f"/tmp/{session_id}.jsonl", "text": "done"}

    runner = BlockingPi()
    app = create_app(Settings(
        agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
        pi_max_active_runs=2, pi_max_active_runs_per_user=1,
    ), pi_runner=runner)
    async with app.router.lifespan_context(app):  # noqa: SIM117 - Scheduler required.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                    base_url="http://test") as client:
            identity = (await client.post("/api/v1/auth/demo",
                                          json={"email": "alice@example.org"})).json()
            headers = {"Authorization": f"Bearer {identity['access_token']}"}
            (await client.get("/api/v1/g", headers=headers)).json()[0]["id"]
            sessions = [(await client.post("/api/v1/c",
                                           headers=headers, json={"title": f"Session {i}"})).json()["id"]
                        for i in range(2)]
            run_ids = [(await client.post(f"/api/v1/c/{session}/messages",
                                          headers=headers, json={"content": "analyze"})).json()["run_id"]
                       for session in sessions]
            for _ in range(100):
                if runner.started:
                    break
                await asyncio.sleep(0.01)
            first = (await client.get(f"/api/v1/runs/{run_ids[0]}", headers=headers)).json()
            second = (await client.get(f"/api/v1/runs/{run_ids[1]}", headers=headers)).json()
            runner.release.set()
            for _ in range(100):
                if len(runner.started) == 2:
                    break
                await asyncio.sleep(0.01)
    assert len(runner.started) == 2
    assert first["status"] == "running" and second["status"] == "queued"
