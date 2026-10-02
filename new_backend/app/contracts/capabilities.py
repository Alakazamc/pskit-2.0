from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class McpTool(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)


class McpInvokeResult(BaseModel):
    tool: str
    status: Literal["completed"] = "completed"
    result: dict[str, Any]
    run_id: str | None = None


class Af3FoldInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    model_seeds: list[int] = Field(alias="modelSeeds", min_length=1, max_length=4)
    sequences: list[dict[str, Any]] = Field(min_length=1, max_length=32)
    dialect: Literal["alphafold3"]
    version: Literal[1, 2, 3, 4]
    bonded_atom_pairs: list[Any] | None = Field(default=None, alias="bondedAtomPairs")
    user_ccd: str | None = Field(default=None, alias="userCCD")

    @model_validator(mode="after")
    def validate_mock_contract(self) -> "Af3FoldInput":
        """Reject invalid molecules, file paths, seeds, and oversized inputs.

        Returns:
            This validated AF3 input.

        Raises:
            ValueError: Input violates the supported AF3 submission contract.
        """
        if not self.name.strip() or any(type(seed) is not int or seed < 0 or seed > 2**32 - 1
                                        for seed in self.model_seeds):
            raise ValueError("Invalid AF3 name or model seed")
        for entity in self.sequences:
            if len(entity) != 1 or next(iter(entity), None) not in {"protein", "rna", "dna", "ligand"}:
                raise ValueError("Each AF3 entity must have one supported molecule type")
            body = next(iter(entity.values()))
            if not isinstance(body, dict) or not body.get("id"):
                raise ValueError("Each AF3 entity must have an ID")
        def contains_path(value: Any) -> bool:
            """Find nested keys that would reference external file paths."""
            if isinstance(value, dict):
                return any(key.endswith("Path") or contains_path(item)
                           for key, item in value.items())
            return isinstance(value, list) and any(contains_path(item) for item in value)
        data = self.model_dump(by_alias=True, exclude_none=True)
        if contains_path(data):
            raise ValueError("External file paths are not accepted; upload files first")
        if len(self.model_dump_json(by_alias=True).encode("utf-8")) > 256 * 1024:
            raise ValueError("AF3 input exceeds 256 KiB")
        return self


class Af3JobRequest(BaseModel):
    estimated_gpu_minutes: int = Field(default=20, ge=1, le=60)
    run_id: str | None = None
    fold_input: Af3FoldInput | None = None


class ComputeResourceRequirements(BaseModel):
    capability: Literal["af3"] = "af3"
    gpu_count: int = Field(default=1, ge=1, le=8)
    min_gpu_memory_mb: int = Field(default=0, ge=0, le=1_048_576)


class ComputeWorkerResources(BaseModel):
    capabilities: set[str] = Field(min_length=1, max_length=32)
    gpu_count: int = Field(ge=0, le=8)
    gpu_memory_mb: int = Field(ge=0, le=1_048_576)


class Af3Job(BaseModel):
    id: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    progress: int = Field(ge=0, le=100)
    estimated_gpu_minutes: int
    actual_gpu_minutes: int | None = None
    gpu_accounting_status: Literal[
        "reserved", "released", "settled", "pending_reconciliation", "reconciled"
    ] = "reserved"
    run_id: str | None = None
    artifacts: list[dict[str, str]] = Field(default_factory=list)
    fold_input: dict[str, Any] | None = None
    simulation: bool = False
    resource_requirements: ComputeResourceRequirements = Field(
        default_factory=ComputeResourceRequirements
    )


class Af3ComputeClaim(Af3Job):
    attempt: int = Field(ge=1)
    lease_token: str


class Af3GpuReconciliation(BaseModel):
    id: int
    job_id: str
    user_id: str
    source: Literal["worker", "admin"]
    previous_minutes: int | None
    actual_minutes: int
    reason: str
    created_at: datetime
