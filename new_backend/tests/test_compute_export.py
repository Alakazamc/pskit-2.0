"""Exported compute schemas stay usable by API consumers and model maintainers."""

import json
import subprocess
from pathlib import Path


def test_export_contains_the_service_return_protocol_and_worker_receipts():
    root = Path(__file__).resolve().parents[1]
    subprocess.run([str(root/".venv/bin/python"), "-m", "scripts.export_openapi"], cwd=root, check=True)
    schema = json.loads((root.parent/"contracts/compute.schema.json").read_text())
    from jsonschema import Draft202012Validator
    for sample in [
        {"status": "pending", "job_id": "external-1"},
        {"status": "failed", "error": {"code": "OOM", "message": "failed"},
         "usage": {"gpu_device_ms": 1234, "cpu_core_ms": None, "source": "service_reported"}},
        {"schema_version": "pskit.compute.v1", "service_id": "lab", "model_version": "v1",
         "capabilities": [{"id": "lab.inspect", "version": "1", "input_schema": {"type": "object"}}]},
    ]:
        Draft202012Validator(schema).validate(sample)
    validator = Draft202012Validator(schema)
    assert not validator.is_valid({"status": "pending", "job_id": "external-1", "usage": {}})
    assert not validator.is_valid({"status": "failed", "error": {"code": "OOM", "message": "failed"},
                                   "usage": {"gpu_device_ms": -1, "source": "measured"}})
    openapi = json.loads((root.parent/"contracts/openapi.json").read_text())
    assert "/api/v1/compute/jobs" in openapi["paths"]
    assert "/internal/compute/jobs/{job_id}/result" in openapi["paths"]
