"""PSKit AI4S 能力清单（Capability Manifest）。

本模块只登记科研能力的身份、输入输出、风险、资源和证据边界，不执行工具，
也不把 Manifest 当作权限授予。旧工具目录仍是兼容运行时的事实来源；这里构造
一份与其并行的新 Harness 视图，供后续 Coordinator/Skill/Executor 审计使用。
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from types import MappingProxyType
from typing import Any, Literal

from app.harness.contracts import CompletionContract, EvidenceContract
from app.tools.catalog import TOOL_CATALOG, ToolSpec, openai_tool_schemas


ExecutionMode = Literal["sync", "async"]
RiskLevel = Literal["read_only", "controlled_write", "high_cost", "destructive"]
Availability = Literal["session_contract", "session_native", "legacy_bridge", "disabled"]
CapabilityType = Literal["external_api", "worker", "legacy_bridge"]

_EXECUTION_MODES = frozenset({"sync", "async"})
_RISK_LEVELS = frozenset({"read_only", "controlled_write", "high_cost", "destructive"})
_AVAILABILITIES = frozenset(
    {"session_contract", "session_native", "legacy_bridge", "disabled"}
)
_JSON_SCHEMA_TYPES = frozenset({"object", "array", "string", "number", "integer", "boolean", "null"})


def _copy_without_research_run_id(value: Any) -> Any:
    """从兼容 Catalog Schema 派生 Session-native Schema。

    这里只清理新能力输入契约；旧 ``openai_tool_schemas`` 继续保留原字段，
    以免改变现有 Agent/Worker 运行时行为。
    """

    if isinstance(value, Mapping):
        result = {
            key: _copy_without_research_run_id(item)
            for key, item in value.items()
            if key != "research_run_id"
        }
        # 新 Harness 输入是显式契约；不允许被清理掉的旧字段以
        # additionalProperties 形式悄悄穿透到 Session-native 执行器。
        if result.get("type") == "object" and "additionalProperties" not in result:
            result["additionalProperties"] = False
        required = result.get("required")
        if isinstance(required, list):
            required = [item for item in required if item != "research_run_id"]
            if required:
                result["required"] = required
            else:
                result.pop("required", None)
        return result
    if isinstance(value, list):
        return [_copy_without_research_run_id(item) for item in value]
    return deepcopy(value)


def _validate_json_schema(schema: Mapping[str, Any], path: str = "schema") -> None:
    """执行不依赖第三方包的 JSON Schema 结构校验。

    能力清单在导入时即校验，避免坏 Schema 进入目录；语义级约束仍由执行层
    使用 Pydantic/领域适配器执行，这里不重复实现业务验证。
    """

    if not isinstance(schema, Mapping):
        raise TypeError(f"{path} must be a JSON object")
    schema_type = schema.get("type")
    if schema_type is not None:
        if isinstance(schema_type, (list, tuple)):
            if not schema_type or any(item not in _JSON_SCHEMA_TYPES for item in schema_type):
                raise ValueError(f"{path}.type contains an invalid JSON Schema type")
        elif schema_type not in _JSON_SCHEMA_TYPES:
            raise ValueError(f"{path}.type is not a valid JSON Schema type")
    if "properties" in schema:
        properties = schema["properties"]
        if not isinstance(properties, Mapping):
            raise TypeError(f"{path}.properties must be an object")
        for name, child in properties.items():
            if not isinstance(name, str) or not name:
                raise ValueError(f"{path}.properties contains an invalid field name")
            _validate_json_schema(child, f"{path}.properties.{name}")
    if "required" in schema:
        required = schema["required"]
        if not isinstance(required, (list, tuple)) or any(
            not isinstance(item, str) for item in required
        ):
            raise TypeError(f"{path}.required must be a list of strings")
        properties = schema.get("properties", {})
        if isinstance(properties, Mapping) and any(item not in properties for item in required):
            raise ValueError(f"{path}.required contains a field absent from properties")
        if len(set(required)) != len(required):
            raise ValueError(f"{path}.required contains duplicate fields")
    if "items" in schema:
        _validate_json_schema(schema["items"], f"{path}.items")
    for keyword in ("anyOf", "oneOf", "allOf"):
        if keyword in schema:
            branches = schema[keyword]
            if not isinstance(branches, (list, tuple)) or not branches:
                raise ValueError(f"{path}.{keyword} must be a non-empty list")
            for index, branch in enumerate(branches):
                _validate_json_schema(branch, f"{path}.{keyword}[{index}]")
    if "enum" in schema and not isinstance(schema["enum"], (list, tuple)):
        raise TypeError(f"{path}.enum must be a list")
    if "additionalProperties" in schema and not isinstance(schema["additionalProperties"], (bool, Mapping)):
        raise TypeError(f"{path}.additionalProperties must be bool or schema")
    if isinstance(schema.get("additionalProperties"), Mapping):
        _validate_json_schema(schema["additionalProperties"], f"{path}.additionalProperties")


def validate_json_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """校验并复制一个输入/输出 JSON Schema。"""

    _validate_json_schema(schema)
    return _json_ready(schema)


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_deep_freeze(item) for item in value)
    return value


def _json_ready(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_ready(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class CapabilityManifest:
    """一项可审计 AI4S 科研能力的声明。"""

    capability_id: str
    display_name: str
    version: str
    description: str
    provider: str
    capability_type: CapabilityType
    execution_mode: ExecutionMode
    risk_level: RiskLevel
    availability: Availability
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    required_permissions: tuple[str, ...]
    resource_profile: Mapping[str, Any]
    completion_contract: CompletionContract
    evidence_contract: EvidenceContract
    dependency_profile: Mapping[str, Any]
    timeout_policy: Mapping[str, Any]
    cancellation_policy: Mapping[str, Any]
    idempotency_policy: Mapping[str, Any]
    data_policy: Mapping[str, Any]
    license_profile: Mapping[str, Any]
    source_profile: Mapping[str, Any]
    supports_resume: bool
    approval_required: bool = False

    def __post_init__(self) -> None:
        for field_name in ("capability_id", "display_name", "version", "description", "provider"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
            object.__setattr__(self, field_name, value.strip())
        if self.execution_mode not in _EXECUTION_MODES:
            raise ValueError(f"unsupported execution_mode: {self.execution_mode!r}")
        if self.risk_level not in _RISK_LEVELS:
            raise ValueError(f"unsupported risk_level: {self.risk_level!r}")
        if self.availability not in _AVAILABILITIES:
            raise ValueError(f"unsupported availability: {self.availability!r}")
        if self.capability_type not in {"external_api", "worker", "legacy_bridge"}:
            raise ValueError(f"unsupported capability_type: {self.capability_type!r}")
        object.__setattr__(
            self,
            "input_schema",
            _deep_freeze(validate_json_schema(self.input_schema)),
        )
        object.__setattr__(
            self,
            "output_schema",
            _deep_freeze(validate_json_schema(self.output_schema)),
        )
        if self.availability in {"session_contract", "session_native"} and _contains_key(
            self.input_schema, "research_run_id"
        ):
            raise ValueError("session capability input_schema must not use research_run_id")
        if isinstance(self.required_permissions, str):
            raise TypeError("required_permissions must be a sequence of strings")
        raw_permissions = tuple(self.required_permissions)
        if any(not isinstance(item, str) or not item.strip() for item in raw_permissions):
            raise ValueError("required_permissions must contain non-empty strings")
        permissions = tuple(item.strip() for item in raw_permissions)
        if len(set(permissions)) != len(permissions):
            raise ValueError("required_permissions must not contain duplicates")
        object.__setattr__(self, "required_permissions", permissions)
        if not isinstance(self.resource_profile, Mapping):
            raise TypeError("resource_profile must be a mapping")
        object.__setattr__(self, "resource_profile", _deep_freeze(self.resource_profile))
        if isinstance(self.completion_contract, Mapping):
            object.__setattr__(
                self,
                "completion_contract",
                CompletionContract(**dict(self.completion_contract)),
            )
        elif not isinstance(self.completion_contract, CompletionContract):
            raise TypeError("completion_contract must be a CompletionContract")
        if isinstance(self.evidence_contract, Mapping):
            object.__setattr__(
                self,
                "evidence_contract",
                EvidenceContract(**dict(self.evidence_contract)),
            )
        elif not isinstance(self.evidence_contract, EvidenceContract):
            raise TypeError("evidence_contract must be an EvidenceContract")
        for field_name in (
            "dependency_profile",
            "timeout_policy",
            "cancellation_policy",
            "idempotency_policy",
            "data_policy",
            "license_profile",
            "source_profile",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, Mapping):
                raise TypeError(f"{field_name} must be a mapping")
            object.__setattr__(self, field_name, _deep_freeze(value))
        if not isinstance(self.supports_resume, bool):
            raise TypeError("supports_resume must be a bool")
        if not isinstance(self.approval_required, bool):
            raise TypeError("approval_required must be a bool")
        if self.risk_level in {"high_cost", "destructive"} and not self.approval_required:
            raise ValueError("high_cost/destructive capabilities must require policy approval")
        if self.approval_required and "policy.approval" not in self.required_permissions:
            raise ValueError("approval_required capability must require policy.approval")

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "display_name": self.display_name,
            "version": self.version,
            "description": self.description,
            "provider": self.provider,
            "capability_type": self.capability_type,
            "execution_mode": self.execution_mode,
            "risk_level": self.risk_level,
            "availability": self.availability,
            "input_schema": _json_ready(self.input_schema),
            "output_schema": _json_ready(self.output_schema),
            "required_permissions": list(self.required_permissions),
            "resource_profile": _json_ready(self.resource_profile),
            "completion_contract": self.completion_contract.to_dict(),
            "evidence_contract": self.evidence_contract.to_dict(),
            "dependency_profile": _json_ready(self.dependency_profile),
            "timeout_policy": _json_ready(self.timeout_policy),
            "cancellation_policy": _json_ready(self.cancellation_policy),
            "idempotency_policy": _json_ready(self.idempotency_policy),
            "data_policy": _json_ready(self.data_policy),
            "license_profile": _json_ready(self.license_profile),
            "source_profile": _json_ready(self.source_profile),
            "supports_resume": self.supports_resume,
            "approval_required": self.approval_required,
        }

    @property
    def enabled(self) -> bool:
        """仅 ``session_native`` 能力可直接进入新 Harness 执行器。"""

        return self.availability == "session_native"

    @property
    def contract_ready(self) -> bool:
        """已具备新 Harness 契约，但尚未证明生产运行时接线。"""

        return self.availability in {"session_contract", "session_native"}

    @property
    def digest(self) -> str:
        payload = json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @property
    def active(self) -> bool:
        return self.enabled

    @property
    def disabled(self) -> bool:
        return self.availability == "disabled"

    @property
    def is_legacy_bridge(self) -> bool:
        return self.availability == "legacy_bridge"


def _contains_key(value: Any, key: str) -> bool:
    if isinstance(value, Mapping):
        return key in value or any(_contains_key(item, key) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, key) for item in value)
    return False


def _output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "result": {},
            "artifacts": {"type": "array", "items": {"type": "string"}},
            "evidence": {"type": "array", "items": {"type": "string"}},
        },
        "additionalProperties": True,
    }


_SESSION_CONTRACT_NAMES = frozenset({"fetch_pdb_info", "search_sequence_homologs"})

_RESEARCH_RUN_BRIDGE_NAMES = frozenset(
    {
        # 这些入口读写旧 ResearchRun 归属，只作为迁移兼容桥接，不是新 Harness 能力。
        # ADR 0012 已删除 get_research_run_state 与 advance_research_stage 两个阶段工具。
        "score_research_candidates",
        "submit_research_top10_af3",
        "generate_research_report",
    }
)

_HIGH_COST_NAMES = frozenset(
    {
        "run_alphafold3",
    }
)

_CONTROLLED_WRITE_NAMES = frozenset(
    {
        "download_pdb_file",
        "split_pdb_by_chain",
        "split_complex",
        "extract_fragment",
        "calculate_contact_map",
        "annotate_binding_pairs",
        "generate_session_report",
        "predict_binding_sites",
        "extract_empirical_features",
        "predict_interaction",
        "search_sequence_homologs",
        "search_structure_homologs",
        "generate_coral_candidates",
        "generate_pepccd_candidates",
    }
)

_READ_ONLY_NAMES = frozenset(
    {
        "search_pdb",
        "serpapi_search",
        "search_uniprot",
        "fetch_uniprot_entry",
        "search_rnacentral",
        "fetch_rnacentral_entry",
        "fetch_pdb_info",
        "read_result_file",
    }
)

_DEPENDENCIES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "search_pdb": ("rcsb_pdb_api",),
        "download_pdb_file": ("rcsb_files",),
        "fetch_pdb_info": ("rcsb_pdb_api",),
        "search_uniprot": ("uniprot_api",),
        "fetch_uniprot_entry": ("uniprot_api",),
        "search_rnacentral": ("rnacentral_api",),
        "fetch_rnacentral_entry": ("rnacentral_api",),
        "serpapi_search": ("serpapi",),
        "search_sequence_homologs": ("sequence_search_runtime", "sequence_database"),
        "search_structure_homologs": ("foldseek_runtime", "foldseek_database"),
        "generate_coral_candidates": ("coral_mcp",),
        "generate_pepccd_candidates": ("pepccd_mcp",),
        "run_alphafold3": ("alphafold3_runtime", "alphafold3_models", "gpu"),
    }
)


def _manifest_for_tool(tool: ToolSpec, schemas: Mapping[str, Mapping[str, Any]]) -> CapabilityManifest:
    name = tool.name
    # wzf：第三批只把一个同步和一个异步纵向切片接入新执行器。目录中其余工具
    # 仍可由旧运行时调用，但不得被能力清单误报为 Session-native 已迁移。
    availability: Availability = (
        "session_contract" if name in _SESSION_CONTRACT_NAMES else "legacy_bridge"
    )
    if availability == "legacy_bridge":
        risk_level: RiskLevel = (
            "read_only"
            if name in _READ_ONLY_NAMES
            else ("high_cost" if name in _HIGH_COST_NAMES else "controlled_write")
        )
        permissions = (
            ("legacy.bridge", "policy.approval")
            if risk_level == "high_cost"
            else ("legacy.bridge",)
        )
    elif name in _HIGH_COST_NAMES:
        risk_level = "high_cost"
        permissions = ("compute.execute", "policy.approval")
    elif name in _CONTROLLED_WRITE_NAMES:
        risk_level = "controlled_write"
        permissions = (
            ("compute.execute", "artifact.write")
            if tool.long_running
            else ("artifact.write",)
        )
    else:
        risk_level = "read_only"
        permissions = ("session.read",)
    approval_required = risk_level in {"high_cost", "destructive"}
    if availability == "session_contract":
        input_schema = _copy_without_research_run_id(schemas[name])
    else:
        # 兼容桥必须忠实保留旧 ToolRunner 的真实输入，包括 research_run_id；
        # 新旧契约不能混成一份“看似 Session-native”的 Schema。
        input_schema = deepcopy(dict(schemas[name]))
    # 未经服务器资源核验前不填写 CPU/内存/GPU 显存等臆测数字；调度器必须在
    # 运行时根据 Worker 探针与策略解析实际资源请求。
    resource = {
        "profile_status": "unverified",
        "resolved_at_runtime": True,
        "worker_class": "science" if tool.long_running else "web",
        "known_gpu_requirement": name == "run_alphafold3",
    }
    if name == "fetch_pdb_info":
        completion = CompletionContract(required_output_fields=("result",))
        evidence = EvidenceContract(
            required_evidence_types=("execution_record",),
            minimum_evidence_count=1,
            require_provenance=True,
            require_hash=True,
            claims_scope=("database_metadata",),
        )
    elif name == "search_sequence_homologs":
        completion = CompletionContract(minimum_artifact_count=1)
        evidence = EvidenceContract(
            required_evidence_types=("execution_record",),
            minimum_evidence_count=1,
            minimum_artifact_count=1,
            require_provenance=True,
            require_hash=True,
            claims_scope=("computational_result",),
        )
    else:
        completion = CompletionContract()
        evidence = EvidenceContract(
            required_evidence_types=("execution_record",),
            minimum_evidence_count=1,
            require_provenance=True,
            require_hash=False,
            reviewer_required=approval_required,
            claims_scope=("computational_result",),
        )
    dependency = {
        "dependencies": list(_DEPENDENCIES.get(name, ())),
        "verification": "runtime_required",
        "health_check": None,
    }
    return CapabilityManifest(
        capability_id=name,
        display_name=name,
        version="1.0.0",
        description=tool.description,
        provider=(
            "legacy_research_run"
            if name in _RESEARCH_RUN_BRIDGE_NAMES
            else ("legacy_tool_runner" if availability == "legacy_bridge" else "pskit_science")
        ),
        capability_type=(
            "external_api"
            if name == "fetch_pdb_info"
            else ("worker" if name == "search_sequence_homologs" else "legacy_bridge")
        ),
        execution_mode="async" if tool.long_running else "sync",
        risk_level=risk_level,
        availability=availability,
        input_schema=input_schema,
        output_schema=_output_schema(),
        required_permissions=permissions,
        resource_profile=resource,
        completion_contract=completion,
        evidence_contract=evidence,
        dependency_profile=dependency,
        timeout_policy={"status": "unverified", "resolved_at_runtime": True},
        cancellation_policy={
            "status": "unverified",
            "resolved_at_runtime": True,
        },
        idempotency_policy={
            "scope": "user_session",
            "key_required": True,
            "replay": "reuse_existing_invocation",
        },
        data_policy={"classification": "policy_resolved", "status": "unverified"},
        license_profile={"status": "unverified"},
        source_profile={
            "catalog": "TOOL_CATALOG",
            "implementation": (
                "session_capability_contract"
                if availability == "session_contract"
                else "legacy_tool_runner"
            ),
            "runtime_wired": False,
        },
        supports_resume=False,
        approval_required=approval_required,
    )


def _build_registry() -> dict[str, CapabilityManifest]:
    catalog_schemas = {
        item["function"]["name"]: item["function"]["parameters"]
        for item in openai_tool_schemas()
    }
    missing = [tool.name for tool in TOOL_CATALOG if tool.name not in catalog_schemas]
    if missing:
        raise RuntimeError(f"TOOL_CATALOG entries missing input schemas: {', '.join(missing)}")
    registry = {
        tool.name: _manifest_for_tool(tool, catalog_schemas)
        for tool in TOOL_CATALOG
    }
    if set(registry) != {tool.name for tool in TOOL_CATALOG}:
        raise RuntimeError("Capability registry does not cover TOOL_CATALOG exactly")
    return registry


_CAPABILITY_REGISTRY = _build_registry()
CAPABILITY_MANIFESTS: Mapping[str, CapabilityManifest] = MappingProxyType(_CAPABILITY_REGISTRY)
# 常用别名：注册表仍是同一份只读映射，不产生第二份状态。
CAPABILITY_REGISTRY = CAPABILITY_MANIFESTS
MANIFESTS = CAPABILITY_MANIFESTS


def list_capability_manifests() -> list[CapabilityManifest]:
    """按兼容 TOOL_CATALOG 顺序返回能力清单。"""

    return [CAPABILITY_MANIFESTS[tool.name] for tool in TOOL_CATALOG]


def get_capability_manifest(capability_id: str) -> CapabilityManifest:
    try:
        return CAPABILITY_MANIFESTS[capability_id]
    except KeyError as exc:
        raise KeyError(f"unknown capability_id: {capability_id}") from exc


def capability_manifest_for(capability_id: str) -> CapabilityManifest:
    return get_capability_manifest(capability_id)


def validate_capability_manifest(manifest: CapabilityManifest) -> CapabilityManifest:
    """对外提供显式验证入口，便于安装能力包时复用。"""

    if not isinstance(manifest, CapabilityManifest):
        raise TypeError("manifest must be a CapabilityManifest")
    # frozen dataclass 的构造过程已执行全部校验；这里再次检查关键边界以便审计调用。
    _validate_json_schema(manifest.input_schema)
    _validate_json_schema(manifest.output_schema)
    if manifest.availability in {"session_contract", "session_native"} and _contains_key(
        manifest.input_schema, "research_run_id"
    ):
        raise ValueError("session capability manifest must not contain research_run_id")
    return manifest


__all__ = [
    "Availability",
    "CAPABILITY_MANIFESTS",
    "CAPABILITY_REGISTRY",
    "CapabilityManifest",
    "CapabilityType",
    "ExecutionMode",
    "MANIFESTS",
    "RiskLevel",
    "capability_manifest_for",
    "get_capability_manifest",
    "list_capability_manifests",
    "validate_capability_manifest",
    "validate_json_schema",
]
