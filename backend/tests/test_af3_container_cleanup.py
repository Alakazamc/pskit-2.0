import json
from types import SimpleNamespace

from app.tasks import worker


def test_af3_timeout_only_cleans_container_with_this_task_mount(monkeypatch, tmp_path):
    output_dir = tmp_path / "task-123"
    output_dir.mkdir()
    monkeypatch.setattr(
        worker,
        "snapshot_active_af3_containers",
        lambda _image: {"existing", "owned", "other"},
    )
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        if command[:2] == ["docker", "inspect"]:
            container_id = command[-1]
            source = (
                output_dir / "input"
                if container_id == "owned"
                else tmp_path / "unrelated" / "input"
            )
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [{"Type": "bind", "Source": str(source), "Destination": "/input"}]
                ),
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(worker.subprocess, "run", fake_run)

    worker.cleanup_new_af3_containers("alphafold3:3.0.1", {"existing"}, output_dir)

    stop_or_remove = [command for command in commands if command[1] in {"stop", "rm"}]
    assert [command[-1] for command in stop_or_remove] == ["owned", "owned"]
    assert "skip other" in (output_dir / "af3_container_cleanup.log").read_text()


def test_af3_timeout_keeps_container_when_mounts_cannot_be_verified(
    monkeypatch, tmp_path
):
    output_dir = tmp_path / "task-123"
    output_dir.mkdir()
    monkeypatch.setattr(
        worker, "snapshot_active_af3_containers", lambda _image: {"unknown"}
    )
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=1, stdout="", stderr="no such container")

    monkeypatch.setattr(worker.subprocess, "run", fake_run)

    worker.cleanup_new_af3_containers("alphafold3:3.0.1", set(), output_dir)

    assert len(commands) == 1
    assert commands[0][:2] == ["docker", "inspect"]
    assert "skip unknown" in (output_dir / "af3_container_cleanup.log").read_text()
