from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.tools.mcp_adapters import PepCCDParameters


def normalized_sequence(value: str, *, alphabet: set[str], label: str) -> str:
    sequence = "".join(str(value or "").split()).upper()
    if not sequence:
        raise ValueError(f"{label} must not be empty")
    invalid = sorted(set(sequence) - alphabet)
    if invalid:
        raise ValueError(
            f"{label} contains unsupported symbols: {''.join(invalid)}"
        )
    return sequence


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResearchBoundInput(StrictInput):
    research_run_id: UUID | None = None


class ArtifactReferenceInput(ResearchBoundInput):
    artifact_id: UUID | None = None
    pdb_path: str | None = Field(default=None, min_length=1, max_length=2048)

    @model_validator(mode="after")
    def require_artifact_reference(self):
        if self.artifact_id is None and self.pdb_path is None:
            raise ValueError("artifact_id or pdb_path is required")
        return self


class BindingSiteInput(ArtifactReferenceInput):
    ligand_type: Literal["DNA", "RNA"] = "RNA"


class EmpiricalFeaturesInput(ArtifactReferenceInput):
    emp_feats: str = Field(default="dssp", min_length=1, max_length=120)
    rosetta_relax: bool = False


class SequenceHomologyInput(ResearchBoundInput):
    protein_sequence: str = Field(min_length=1, max_length=10000)
    max_hits: int = Field(default=50, ge=1, le=500)
    evalue: float = Field(default=1e-3, gt=0)
    sensitivity: float = Field(default=7.5, gt=0, le=20)

    @field_validator("protein_sequence")
    @classmethod
    def validate_protein(cls, value: str) -> str:
        return normalized_sequence(
            value,
            alphabet=set("ACDEFGHIKLMNPQRSTVWY"),
            label="protein_sequence",
        )


class StructureHomologyInput(ArtifactReferenceInput):
    chain: str | None = Field(default=None, min_length=1, max_length=4)
    max_hits: int = Field(default=50, ge=1, le=500)
    evalue: float = Field(default=1e-3, gt=0)
    sensitivity: float = Field(default=9.5, gt=0, le=20)


class InteractionInput(ResearchBoundInput):
    protein_sequence: str | None = Field(default=None, max_length=10000)
    nucleic_sequence: str | None = Field(default=None, max_length=10000)
    protein_seq: str | None = Field(default=None, max_length=10000)
    nucleic_acid_seq: str | None = Field(default=None, max_length=10000)

    @model_validator(mode="after")
    def normalize_sequences(self):
        self.protein_sequence = normalized_sequence(
            self.protein_sequence or self.protein_seq or "",
            alphabet=set("ACDEFGHIKLMNPQRSTVWY"),
            label="protein_sequence",
        )
        self.nucleic_sequence = normalized_sequence(
            self.nucleic_sequence or self.nucleic_acid_seq or "",
            alphabet=set("ACGTU"),
            label="nucleic_sequence",
        )
        self.protein_seq = None
        self.nucleic_acid_seq = None
        return self


class CoralInput(ResearchBoundInput):
    research_run_id: UUID
    pdb_id: str = Field(pattern=r"^[0-9A-Za-z]{4}$")
    chain: str = Field(min_length=1, max_length=4)
    num_candidates: int | None = Field(default=None, ge=1, le=100)
    num_samples: int | None = Field(default=None, ge=1, le=100)
    length: int | None = Field(default=None, ge=1, le=200)
    iteration: int | None = Field(default=None, ge=0)

    @field_validator("pdb_id")
    @classmethod
    def normalize_pdb_id(cls, value: str) -> str:
        return value.upper()


class Af3Entity(StrictInput):
    type: Literal["protein", "rna"]
    sequence: str = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def validate_sequence(self):
        alphabet = (
            set("ACDEFGHIKLMNPQRSTVWY")
            if self.type == "protein"
            else set("ACGU")
        )
        self.sequence = normalized_sequence(
            self.sequence,
            alphabet=alphabet,
            label=f"{self.type} sequence",
        )
        return self


class Alphafold3Input(ResearchBoundInput):
    candidate_id: UUID | None = None
    target_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    candidate_sequence_hash: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    job_name: str | None = Field(default=None, min_length=1, max_length=120)
    entities: list[Af3Entity] = Field(min_length=1, max_length=20)
    model_seed: int = Field(default=42, ge=0, le=4294967295)
    num_diffusion_samples: int = Field(default=5, ge=1, le=20)
    max_template_date: str = Field(
        default="2021-09-30",
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    )

    @model_validator(mode="after")
    def validate_research_binding(self):
        context_fields = (
            self.research_run_id,
            self.candidate_id,
            self.target_hash,
            self.candidate_sequence_hash,
        )
        if any(value is not None for value in context_fields) and not all(
            value is not None for value in context_fields
        ):
            raise ValueError(
                "research AF3 input requires run, candidate and both hashes"
            )
        return self


class PepCCDInput(PepCCDParameters):
    model_config = ConfigDict(extra="forbid")
    research_run_id: UUID
    iteration: int | None = Field(default=None, ge=0)


TASK_INPUT_MODELS: dict[str, type[BaseModel]] = {
    "predict_binding_sites": BindingSiteInput,
    "predict_interaction": InteractionInput,
    "extract_empirical_features": EmpiricalFeaturesInput,
    "search_sequence_homologs": SequenceHomologyInput,
    "search_structure_homologs": StructureHomologyInput,
    "generate_coral_candidates": CoralInput,
    "generate_pepccd_candidates": PepCCDInput,
    "run_alphafold3": Alphafold3Input,
}


def validate_task_input(task_type: str, input_json: dict) -> dict:
    if not isinstance(input_json, dict):
        raise ValueError("Task input must be an object")
    model = TASK_INPUT_MODELS.get(task_type)
    if model is None:
        raise ValueError(f"Unsupported worker task type: {task_type}")
    try:
        validated = model.model_validate(input_json)
    except ValidationError as exc:
        fields = sorted(
            {
                ".".join(str(part) for part in item.get("loc") or ("input",))
                for item in exc.errors(include_input=False)
            }
        )
        raise ValueError(
            f"Invalid task input fields: {', '.join(fields)}"
        ) from exc
    return validated.model_dump(mode="json", exclude_none=True)
