"""Server-owned model discovery and image-capability policy for LiteLLM aliases."""

import asyncio
from time import monotonic
from typing import Literal

import httpx
from pydantic import BaseModel, Field


class ModelOption(BaseModel):
    """Public metadata for one gateway-visible model alias."""

    id: str = Field(min_length=1, max_length=200)
    supports_images: bool = False
    reasoning_levels: list[Literal["off", "minimal", "low", "medium", "high", "xhigh", "max"]] = Field(default_factory=list)


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
    enabled = {level for level, flag in flags.items()
               if all(item.get(flag) is True for item in metadata)}
    enabled.update(("medium", "high"))
    return [level for level in ("off", "minimal", "low", "medium", "high", "xhigh", "max")
            if level in enabled]


class ModelCatalog:
    """Cache visible aliases without exposing gateway keys or deployment details."""

    def __init__(
        self, *, base_url: str, api_key: str, default_model: str,
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
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def list_models(self) -> tuple[ModelOption, ...]:
        """List aliases allowed by the gateway key; unknown vision support is false."""
        if self._cache and monotonic() < self._expires_at:
            return self._cache
        async with self._lock:
            if self._cache and monotonic() < self._expires_at:
                return self._cache
            ids: set[str] = set()
            info: dict[str, list[bool]] = {}
            reasoning_info: dict[str, list[dict]] = {}
            deployment_info: dict[str, list[dict]] = {}
            non_chat: set[str] = set()
            if self.base_url and self.api_key:
                async with httpx.AsyncClient(
                    base_url=self.base_url, transport=self.transport,
                    timeout=5, trust_env=False,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                ) as client:
                    try:
                        listing = await client.get("/v1/models")
                        listing.raise_for_status()
                        for item in listing.json().get("data", []):
                            model_id = item.get("id") if isinstance(item, dict) else None
                            if isinstance(model_id, str) and 0 < len(model_id) <= 200 and "*" not in model_id:
                                ids.add(model_id)
                                metadata = item.get("model_info") or {}
                                if isinstance(metadata, dict):
                                    reasoning_info.setdefault(model_id, []).append(metadata)
                                    if isinstance(metadata.get("mode"), str) and metadata["mode"] != "chat":
                                        non_chat.add(model_id)
                                    if "supports_vision" in metadata:
                                        info.setdefault(model_id, []).append(
                                            metadata["supports_vision"] is True,
                                        )
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
                                if model_id in ids and isinstance(metadata, dict):
                                    deployment_info.setdefault(model_id, []).append(metadata)
                                    if isinstance(metadata.get("mode"), str) and metadata["mode"] != "chat":
                                        non_chat.add(model_id)
                                    info.setdefault(model_id, []).append(
                                        metadata.get("supports_vision") is True,
                                    )
                        except (httpx.HTTPError, ValueError, AttributeError):
                            pass
            if not ids and self.default_model and "*" not in self.default_model and self.default_model not in non_chat:
                ids.add(self.default_model)
            ids.difference_update(non_chat)
            self._cache = tuple(ModelOption(
                id=model_id,
                supports_images=(model_id in self.image_model_ids or bool(info.get(model_id))
                                 and all(info[model_id])),
                reasoning_levels=_reasoning_levels(deployment_info.get(model_id, reasoning_info.get(model_id, []))),
            ) for model_id in sorted(ids))
            self._expires_at = monotonic() + self.ttl_seconds
            return self._cache

    async def resolve(self, requested: str | None) -> ModelOption:
        """Select a visible alias, using the configured default when omitted."""
        models = await self.list_models()
        preferred = (self.default_model if any(item.id == self.default_model for item in models)
                     else models[0].id if models else "")
        selected = requested or preferred
        for item in models:
            if item.id == selected:
                return item
        raise ValueError("Model is unavailable")
