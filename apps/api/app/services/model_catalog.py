"""Server-owned model discovery and image-capability policy for LiteLLM aliases."""

import asyncio
from time import monotonic
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from app.contracts.model_runtime import ModelRuntimeProfile, fingerprint


class ModelOption(BaseModel):
    """Public metadata for one gateway-visible model alias."""

    id: str = Field(min_length=1, max_length=200)
    supports_images: bool = False
    reasoning_levels: list[Literal["off", "minimal", "low", "medium", "high", "xhigh", "max"]] = (
        Field(default_factory=list)
    )
    display_name: str | None = Field(default=None, exclude_if=lambda value: value is None)
    provider_label: str | None = Field(default=None, exclude_if=lambda value: value is None)
    runtime_profile: ModelRuntimeProfile | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )


def _reasoning_levels(metadata: list[dict]) -> list[str]:
    """Expose only levels positively supported by all available metadata."""
    if not metadata or not all(item.get("supports_reasoning") is True for item in metadata):
        return []
    flags = {
        "off": "supports_none_reasoning_effort",
        "minimal": "supports_minimal_reasoning_effort",
        "low": "supports_low_reasoning_effort",
        "xhigh": "supports_xhigh_reasoning_effort",
        "max": "supports_max_reasoning_effort",
    }
    enabled = {
        level for level, flag in flags.items() if all(item.get(flag) is True for item in metadata)
    }
    enabled.update(("medium", "high"))
    return [
        level
        for level in ("off", "minimal", "low", "medium", "high", "xhigh", "max")
        if level in enabled
    ]


def _vision_supported(listing: list[dict], deployments: list[dict], manual: bool) -> bool:
    if deployments:
        return (all(item.get("supports_vision") is True for item in deployments)
                and not any(item.get("supports_vision") is False for item in listing))
    if len(listing) > 1 or any("supports_vision" in item for item in listing):
        return bool(listing) and all(item.get("supports_vision") is True for item in listing)
    # Existing operator override remains a legacy fallback for one unknown generic alias.
    return manual


class ModelCatalog:
    """Cache visible aliases without exposing gateway keys or deployment details."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        default_model: str,
        image_model_ids: set[str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        ttl_seconds: float = 60,
    ) -> None:
        self.base_url = base_url.rstrip("/").removesuffix("/v1")
        self.api_key = api_key
        self.default_model = default_model
        self.image_model_ids = image_model_ids or set()
        self.transport = transport
        self.ttl_seconds = ttl_seconds
        self._cache: tuple[ModelOption, ...] = ()
        self._cache_from_gateway = False
        self._expires_at = 0.0
        self._lock = asyncio.Lock()
        self._deployments: dict[str, tuple[dict, ...]] = {}
        self._capability_observations: dict[str, tuple[dict, ...]] = {}

    async def list_models(self, *, include_fallback: bool = True) -> tuple[ModelOption, ...]:
        """List aliases allowed by the gateway key; unknown vision support is false."""
        if self._cache and monotonic() < self._expires_at:
            return self._cache if include_fallback or self._cache_from_gateway else ()
        async with self._lock:
            if self._cache and monotonic() < self._expires_at:
                return self._cache if include_fallback or self._cache_from_gateway else ()
            ids: set[str] = set()
            reasoning_info: dict[str, list[dict]] = {}
            deployment_info: dict[str, list[dict]] = {}
            deployment_routes: dict[str, list[dict]] = {}
            non_chat: set[str] = set()
            if self.base_url and self.api_key:
                async with httpx.AsyncClient(
                    base_url=self.base_url,
                    transport=self.transport,
                    timeout=5,
                    trust_env=False,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                ) as client:
                    try:
                        listing = await client.get("/v1/models")
                        listing.raise_for_status()
                        for item in listing.json().get("data", []):
                            model_id = item.get("id") if isinstance(item, dict) else None
                            if (
                                isinstance(model_id, str)
                                and 0 < len(model_id) <= 200
                                and "*" not in model_id
                            ):
                                ids.add(model_id)
                                metadata = item.get("model_info") or {}
                                if isinstance(metadata, dict):
                                    reasoning_info.setdefault(model_id, []).append(metadata)
                                    if (
                                        isinstance(metadata.get("mode"), str)
                                        and metadata["mode"] != "chat"
                                    ):
                                        non_chat.add(model_id)
                    except (httpx.HTTPError, ValueError, AttributeError):
                        ids = set()
                    if ids:
                        try:
                            details = await client.get("/model/info")
                            details.raise_for_status()
                            for item in details.json().get("data", []):
                                if not isinstance(item, dict):
                                    continue
                                model_id = item.get("model_name") or item.get("id")
                                metadata = item.get("model_info") or {}
                                if model_id in ids:
                                    if not isinstance(metadata, dict):
                                        metadata = {}
                                    deployment_info.setdefault(model_id, []).append(metadata)
                                    params = item.get("litellm_params")
                                    params = params if isinstance(params, dict) else {}
                                    deployment_routes.setdefault(model_id, []).append({
                                        "metadata": metadata,
                                        "model": params.get("model"),
                                        "api_base": params.get("api_base"),
                                    })
                                    if (
                                        isinstance(metadata.get("mode"), str)
                                        and metadata["mode"] != "chat"
                                    ):
                                        non_chat.add(model_id)
                        except (httpx.HTTPError, ValueError, AttributeError):
                            pass
            self._cache_from_gateway = bool(ids)
            if (
                not ids
                and self.default_model
                and "*" not in self.default_model
                and self.default_model not in non_chat
            ):
                ids.add(self.default_model)
            ids.difference_update(non_chat)
            self._cache = tuple(
                ModelOption(
                    id=model_id,
                    supports_images=_vision_supported(
                        reasoning_info.get(model_id, []), deployment_info.get(model_id, []),
                        model_id in self.image_model_ids,
                    ),
                    reasoning_levels=_reasoning_levels(
                        deployment_info.get(model_id, reasoning_info.get(model_id, []))
                    ),
                )
                for model_id in sorted(ids)
            )
            self._deployments = {
                alias: tuple(deployment_routes.get(alias, reasoning_info.get(alias, [{}])))
                for alias in ids
            }
            self._capability_observations = {
                alias: tuple(reasoning_info.get(alias, []) + deployment_info.get(alias, []))
                for alias in ids
            }
            self._expires_at = monotonic() + self.ttl_seconds
            return self._cache if include_fallback or self._cache_from_gateway else ()

    def deployment_fingerprint(self, alias: str) -> str:
        """Bind qualification to discovered deployments without publishing their metadata."""
        return fingerprint({"gateway": self.base_url, "alias": alias,
                            "deployments": self._deployments.get(alias, ())})

    def known_incompatible(self, alias: str, profile: ModelRuntimeProfile) -> bool:
        """Manual declarations may fill unknown metadata, never contradict known false."""
        metadata = self._capability_observations.get(alias, ())
        flags = {"supports_vision": profile.supports_images,
                 "supports_function_calling": profile.supports_tools,
                 "supports_reasoning": bool(profile.reasoning_levels)}
        flags.update({f"supports_{'none' if level == 'off' else level}_reasoning_effort": True
                      for level in profile.reasoning_levels})
        return any(item.get(flag) is False for item in metadata
                   for flag, declared in flags.items() if declared)

    def invalidate(self) -> None:
        """A qualification probe must refresh discovery rather than bless stale cache."""
        self._expires_at = 0

    async def resolve(self, requested: str | None) -> ModelOption:
        """Select a visible alias, using the configured default when omitted."""
        models = await self.list_models()
        preferred = (
            self.default_model
            if any(item.id == self.default_model for item in models)
            else models[0].id
            if models
            else ""
        )
        selected = requested or preferred
        for item in models:
            if item.id == selected:
                return item
        raise ValueError("Model is unavailable")
