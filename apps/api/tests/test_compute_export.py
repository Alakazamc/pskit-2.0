"""Exported compute schemas stay usable by API consumers and model maintainers."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def test_export_contains_the_service_return_protocol_and_worker_receipts(tmp_path):
    root = Path(__file__).resolve().parents[1]
    script = tmp_path / "apps/api/scripts/export_openapi.py"
    script.parent.mkdir(parents=True)
    shutil.copyfile(root / "scripts/export_openapi.py", script)
    (tmp_path / "contracts").mkdir()
    (tmp_path / "apps/web/src/api").mkdir(parents=True)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("RESEARCH_AGENT_", "SUPABASE_", "MODEL_GATEWAY_", "NEW_API_"))
           and key != "TEST_POSTGRES_DSN"}
    env.update(PYTHONPATH=str(root), RESEARCH_AGENT_MODE="mock", RESEARCH_AGENT_RUNTIME="mock")
    subprocess.run([sys.executable, str(script)], cwd=tmp_path, env=env, check=True, timeout=60)
    schema = json.loads((tmp_path / "contracts/compute.schema.json").read_text())
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
    openapi = json.loads((tmp_path / "contracts/openapi.json").read_text())
    assert "/api/v1/compute/jobs" in openapi["paths"]
    assert "/internal/compute/jobs/{job_id}/result" in openapi["paths"]
