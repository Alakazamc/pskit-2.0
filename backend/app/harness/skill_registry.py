"""AI4S aptamer_closed_loop Skill 的只读注册表。

wzf：注册表只把既有 aptamer_skill 的领域阶段映射到现有
CapabilityManifest，必要时为 Skill 内部的策略反馈节点登记显式
legacy_bridge 占位契约；不授予权限、不开启 feature flag，也不调用科学工具。
legacy_bridge 能力可以作为迁移来源被登记，但不会被本注册表误报为
Session-native 可执行能力。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from app.harness.aptamer_skill import (
    AptamerSkillManifest,
    SKILL_ID,
    StageSpec,
    get_aptamer_closed_loop_manifest,
)
from app.harness.capabilities import CAPABILITY_MANIFESTS, CapabilityManifest
from app.harness.contracts import CompletionContract, EvidenceContract


class SkillRegistryError(RuntimeError):
    """Skill 注册表无法提供一致契约时抛出的异常。"""


_INTERNAL_LEGACY_CAPABILITIES = frozenset({"record_strategy_feedback"})


def _internal_legacy_manifest(capability_id: str) -> CapabilityManifest:
    """为 Skill 编排动作登记非工具占位；它永远不是 Session-native。"""

    return CapabilityManifest(
        capability_id=capability_id,
        display_name=capability_id,
        version="legacy-contract",
        description="Skill 内部编排动作，等待受控旧运行时桥接",
        provider="legacy_skill_orchestrator",
        capability_type="legacy_bridge",
        execution_mode="async",
        risk_level="controlled_write",
        availability="legacy_bridge",
        input_schema={"type": "object", "additionalProperties": True},
        output_schema={"type": "object", "additionalProperties": True},
        required_permissions=("legacy.bridge",),
        resource_profile={"profile_status": "unverified", "resolved_at_runtime": True},
        completion_contract=CompletionContract(),
        evidence_contract=EvidenceContract(
            required_evidence_types=("execution_record",),
            minimum_evidence_count=1,
            require_provenance=True,
            require_hash=False,
            claims_scope=("computational_result",),
        ),
        dependency_profile={"status": "unverified"},
        timeout_policy={"status": "unverified"},
        cancellation_policy={"status": "unverified"},
        idempotency_policy={
            "scope": "user_session",
            "key_required": True,
            "replay": "reuse_existing_invocation",
        },
        data_policy={"classification": "policy_resolved", "status": "unverified"},
        license_profile={"status": "unverified"},
        source_profile={
            "catalog": "aptamer_skill_internal",
            "implementation": "legacy_bridge",
            "runtime_wired": False,
        },
        supports_resume=True,
    )


@dataclass(frozen=True, slots=True)
class StageCapabilityBinding:
    """一个领域阶段与能力清单的只读映射。"""

    stage: StageSpec
    manifests: tuple[CapabilityManifest, ...]
    missing_capabilities: tuple[str, ...]

    @property
    def capability_ids(self) -> tuple[str, ...]:
        return self.stage.capabilities

    @property
    def contract_ready(self) -> bool:
        # legacy_bridge 也可以参与“计划契约”装配；native_ready 单独反映
        # 是否已经具备真实 Session-native 接线。
        return not self.missing_capabilities and len(self.manifests) == len(
            self.stage.capabilities
        )

    @property
    def native_ready(self) -> bool:
        return self.contract_ready and all(
            manifest.availability == "session_native" for manifest in self.manifests
        )

    @property
    def legacy_capabilities(self) -> tuple[str, ...]:
        return tuple(
            manifest.capability_id
            for manifest in self.manifests
            if manifest.availability == "legacy_bridge"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage.stage_id,
            "capability_ids": list(self.capability_ids),
            "missing_capabilities": list(self.missing_capabilities),
            "contract_ready": self.contract_ready,
            "native_ready": self.native_ready,
            "legacy_capabilities": list(self.legacy_capabilities),
            "manifests": [manifest.to_dict() for manifest in self.manifests],
        }


@dataclass(frozen=True, slots=True)
class RegisteredSkill:
    """已注册 Skill 的只读视图。"""

    manifest: AptamerSkillManifest
    stage_bindings: tuple[StageCapabilityBinding, ...]

    @property
    def skill_id(self) -> str:
        return self.manifest.skill_id

    @property
    def version(self) -> str:
        return self.manifest.version

    @property
    def missing_capabilities(self) -> tuple[str, ...]:
        return tuple(
            capability
            for binding in self.stage_bindings
            for capability in binding.missing_capabilities
        )

    @property
    def contract_ready(self) -> bool:
        return not self.missing_capabilities and all(
            binding.contract_ready for binding in self.stage_bindings
        )

    @property
    def native_ready(self) -> bool:
        return self.contract_ready and all(
            binding.native_ready for binding in self.stage_bindings
        )

    @property
    def by_stage(self) -> Mapping[str, StageCapabilityBinding]:
        return MappingProxyType(
            {binding.stage.stage_id: binding for binding in self.stage_bindings}
        )

    def stage(self, stage_id: str) -> StageCapabilityBinding:
        try:
            return self.by_stage[stage_id]
        except KeyError as exc:
            raise SkillRegistryError(f"unknown skill stage: {stage_id}") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "version": self.version,
            "contract_ready": self.contract_ready,
            "native_ready": self.native_ready,
            "missing_capabilities": list(self.missing_capabilities),
            "manifest": self.manifest.to_dict(),
            "stages": [binding.to_dict() for binding in self.stage_bindings],
        }


class AptamerSkillRegistry:
    """只读的 aptamer_closed_loop 注册表。"""

    def __init__(
        self,
        capability_manifests: Mapping[str, CapabilityManifest] | None = None,
        *,
        skill_manifest: AptamerSkillManifest | None = None,
    ) -> None:
        source = CAPABILITY_MANIFESTS if capability_manifests is None else capability_manifests
        if not isinstance(source, Mapping):
            raise TypeError("capability_manifests must be a mapping")
        copied: dict[str, CapabilityManifest] = {}
        for capability_id, manifest in source.items():
            if not isinstance(capability_id, str) or not capability_id.strip():
                raise ValueError("capability manifest key must be non-empty")
            if not isinstance(manifest, CapabilityManifest):
                raise TypeError(f"invalid CapabilityManifest for {capability_id}")
            if manifest.capability_id != capability_id:
                raise ValueError(f"capability key does not match manifest: {capability_id}")
            copied[capability_id] = manifest
        for capability_id in _INTERNAL_LEGACY_CAPABILITIES:
            copied.setdefault(capability_id, _internal_legacy_manifest(capability_id))
        manifest = skill_manifest or get_aptamer_closed_loop_manifest()
        if not isinstance(manifest, AptamerSkillManifest):
            raise TypeError("skill_manifest must be an AptamerSkillManifest")
        if manifest.skill_id != SKILL_ID:
            raise ValueError("registry only accepts aptamer_closed_loop")
        bindings: list[StageCapabilityBinding] = []
        for stage in manifest.stages:
            resolved = tuple(copied[item] for item in stage.capabilities if item in copied)
            missing = tuple(item for item in stage.capabilities if item not in copied)
            bindings.append(
                StageCapabilityBinding(
                    stage=stage,
                    manifests=resolved,
                    missing_capabilities=missing,
                )
            )
        self._manifests = MappingProxyType(copied)
        self._skills = MappingProxyType(
            {
                manifest.skill_id: RegisteredSkill(
                    manifest=manifest,
                    stage_bindings=tuple(bindings),
                )
            }
        )

    def get_skill(self, skill_id: str) -> RegisteredSkill | None:
        if not isinstance(skill_id, str):
            return None
        return self._skills.get(skill_id)

    def get_manifest(self, skill_id: str) -> RegisteredSkill | None:
        """与 CapabilityRegistry 命名保持一致的只读别名。"""

        return self.get_skill(skill_id)

    def require_skill(self, skill_id: str = SKILL_ID) -> RegisteredSkill:
        skill = self.get_skill(skill_id)
        if skill is None:
            raise SkillRegistryError(f"unknown skill: {skill_id}")
        return skill

    def get_capability(self, capability_id: str) -> CapabilityManifest | None:
        return self._manifests.get(capability_id)

    def list_skills(self) -> tuple[RegisteredSkill, ...]:
        return tuple(self._skills.values())

    def to_dict(self) -> dict[str, Any]:
        return {"skills": [skill.to_dict() for skill in self._skills.values()]}


def get_aptamer_skill_registry() -> AptamerSkillRegistry:
    """返回独立只读注册表；不会共享可变执行状态。"""

    return AptamerSkillRegistry()


def get_registered_aptamer_skill() -> RegisteredSkill:
    return get_aptamer_skill_registry().require_skill(SKILL_ID)


__all__ = [
    "AptamerSkillRegistry",
    "RegisteredSkill",
    "SkillRegistryError",
    "StageCapabilityBinding",
    "get_aptamer_skill_registry",
    "get_registered_aptamer_skill",
]
