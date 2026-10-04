"""Memory-only avatar storage for explicitly selected mock environments."""


class MockAvatarStorage:
    def __init__(self) -> None:
        """Start with no avatar objects."""
        self.objects: dict[str, bytes] = {}

    async def put(self, key: str, content: bytes) -> None:
        """Save immutable image bytes."""
        self.objects[key] = content

    async def get(self, key: str) -> bytes | None:
        """Read image bytes if present."""
        return self.objects.get(key)

    async def delete(self, key: str) -> None:
        """Forget an obsolete object if present."""
        self.objects.pop(key, None)
