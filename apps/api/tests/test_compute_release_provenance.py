"""Published release provenance stays outside the preupgrade worker protocol."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.contracts.compute import ComputeJob, ComputeJobRequest, ExecutionBindingSnapshot
from app.domain.compute.common import payload_hash
from app.domain.compute.jobs import ComputeJobs

LEGACY_BINDING = {
    "adapter": "job_mcp", "endpoint_url": "http://127.0.0.1:9/mcp", "credential_ref": None,
    "submit_tool": "echo_document", "status_tool": "document_status", "cancel_tool": None,
    "artifact_tool": None, "submit_job_id_argument": None, "remote_output_schema": {"type": "object"},
    "result_mapping": {"kind": "json_pointer", "pointer": "/result", "renames": {}, "transforms": []},
}


class LegacyBinding(BaseModel):
    """Frozen preupgrade strict receiver shape: provenance is not a wire field."""

    model_config = ConfigDict(extra="forbid")
    adapter: Literal["immediate_mcp", "job_mcp", "mcp_tasks"]
    endpoint_url: str
    credential_ref: str | None = None
    submit_tool: str
    status_tool: str | None = None
    cancel_tool: str | None = None
    artifact_tool: str | None = None
    submit_job_id_argument: str | None = None
    remote_output_schema: dict[str, Any]
    result_mapping: dict[str, Any]


class LegacyExecutionGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job: ComputeJob
    worker_id: str
    attempt: int
    fencing_token: str
    stop_at: datetime
    lease_expires_at: datetime
    gpu_uuids: list[str] = Field(default_factory=list)
    execution_binding: LegacyBinding | None = None
    recovered: bool = False


def test_server_only_release_identity_changes_digest_without_changing_binding_wire_bytes():
    request = ComputeJobRequest(capability_id="documents.echo", version="7", arguments={"text": "echo"})
    binding = ExecutionBindingSnapshot.model_validate(LEGACY_BINDING)
    hashes = [ComputeJobs._submission_hash(
        request, run_id="run-echo", tool_call_id="call-echo", execution_binding=binding,
        provenance={"release_id": release_id, "action_id": "echo"},
    ) for release_id in ("release-documents-1", "release-documents-2")]
    assert hashes[0] != hashes[1]
    assert binding.model_dump(mode="json") == LEGACY_BINDING
    expected = LegacyBinding.model_validate(LEGACY_BINDING).model_dump_json()
    assert binding.model_dump_json() == expected


def test_absent_server_provenance_preserves_exact_legacy_submission_hash():
    request = ComputeJobRequest(capability_id="documents.echo", version="7", arguments={"text": "echo"})
    binding = ExecutionBindingSnapshot.model_validate(LEGACY_BINDING)
    original = {**request.model_dump(mode="json"), "run_id": "run-echo", "tool_call_id": "call-echo",
                "execution_binding": LEGACY_BINDING, "resolved_capability": None}
    assert ComputeJobs._submission_hash(
        request, run_id="run-echo", tool_call_id="call-echo", execution_binding=binding,
    ) == payload_hash(original)
