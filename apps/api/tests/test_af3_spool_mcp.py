"""The AF3 MCP service preserves inputs and outcomes across receiver restarts."""

import base64
import json

import pytest

from pskit_compute.af3_spool import Af3Spool

JOB = "431b6078-4dab-4f6f-8a0b-27591f4d56c4"
INPUT = {
    "name": "mcp-test", "dialect": "alphafold3", "version": 1,
    "modelSeeds": [1],
    "sequences": [{"protein": {
        "id": "A", "sequence": "G", "unpairedMsa": "",
        "pairedMsa": "", "templates": [],
    }}],
}


def test_generic_compute_id_uses_a_native_uuid_spool_directory(tmp_path):
    import uuid

    from scripts.af3_receiver import JOB_ID

    job_id = "compute-" + "a" * 32
    spool = Af3Spool(tmp_path)
    assert spool.submit(job_id, INPUT)["job_id"] == job_id
    directory = spool.directory(job_id)
    assert JOB_ID.fullmatch(directory.name)
    assert str(uuid.UUID(directory.name)) == directory.name
    assert directory == Af3Spool(tmp_path).directory(job_id)
    assert (directory / "input.json").exists()
    assert not (tmp_path / job_id).exists()


def test_invalid_af3_input_is_a_zero_usage_terminal_rejection(tmp_path):
    spool = Af3Spool(tmp_path)
    invalid = {**INPUT, "sequences": [{}]}
    report = spool.submit("compute-" + "b" * 32, invalid)
    assert report["status"] == "failed"
    assert report["error"]["code"] == "INVALID_AF3_INPUT"
    assert report["usage"]["gpu_device_ms"] == 0
    assert list(tmp_path.iterdir()) == []


def test_restarted_service_returns_same_pending_job_without_overwriting_input(tmp_path):
    first = Af3Spool(tmp_path)
    assert first.submit(JOB, INPUT) == {"status": "pending", "job_id": JOB}
    modified = (tmp_path / JOB / "input.json").stat().st_mtime_ns
    resumed = Af3Spool(tmp_path)
    assert resumed.submit(JOB, INPUT) == {"status": "pending", "job_id": JOB}
    assert resumed.status(JOB) == {"status": "pending", "job_id": JOB}
    assert (tmp_path / JOB / "input.json").stat().st_mtime_ns == modified
    different = {**INPUT, "name": "different"}
    with pytest.raises(ValueError, match="AF3_INPUT_CONFLICT"):
        resumed.submit(JOB, different)
    assert json.loads((tmp_path / JOB / "input.json").read_text())["name"] == "mcp-test"


def test_finished_model_exposes_artifacts_and_honest_legacy_usage(tmp_path):
    service = Af3Spool(tmp_path)
    service.submit(JOB, INPUT)
    output = tmp_path / JOB / "output"
    output.mkdir()
    (output / "model.cif").write_text("data_model\n#\n")
    (tmp_path / JOB / "outcome.json").write_text(json.dumps({
        "status": "completed", "actual_gpu_minutes": 2, "simulation": False,
    }))
    report = service.status(JOB)
    assert report["status"] == "completed"
    assert report["usage"]["gpu_device_ms"] == 120000
    assert report["usage"]["cpu_core_ms"] is None
    assert report["usage"]["wall_ms"] is None
    assert report["usage"]["source"] == "estimated"
    assert report["artifacts"][0]["name"] == "model.cif"
    assert report["artifacts"][0]["available"] is True
    assert (tmp_path / JOB / "outcome.json").exists()
    artifact = report["artifacts"][0]
    chunk = service.read_artifact(JOB, artifact["id"], 0, 8)
    assert base64.b64decode(chunk["data_base64"]) == b"data_mod"
    assert chunk["eof"] is False
    with pytest.raises(ValueError, match="ARTIFACT_NOT_FOUND"):
        service.read_artifact(JOB, "unknown-id", 0, 8)
