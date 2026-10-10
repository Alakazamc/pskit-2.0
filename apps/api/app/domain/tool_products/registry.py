"""Small public lookup boundary over immutable Tool Product releases."""

from app.contracts.tool_products import PublishedToolProduct, ToolProductPage


class ToolProductNotAvailable(LookupError):
    """A product is unknown, unpublished, suspended, or hidden from the actor."""


class ToolProductRegistry:
    def __init__(self, repository):
        self.repository = repository

    def resolve(self, slug: str, user_id: str | None) -> PublishedToolProduct:
        release = self.repository.resolve_release(slug)
        if release is None:
            raise ToolProductNotAvailable("TOOL_PRODUCT_NOT_AVAILABLE")
        visible_ids = {item.release_id for item in self.repository.list_visible(user_id)}
        if release.release_id not in visible_ids:
            raise ToolProductNotAvailable("TOOL_PRODUCT_NOT_AVAILABLE")
        return release

    def list_visible(
        self,
        user_id: str | None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> ToolProductPage:
        if limit < 1 or limit > 100:
            raise ValueError("INVALID_PAGE_LIMIT")
        visible = self.repository.list_visible(user_id)
        if cursor:
            visible = [item for item in visible if item.slug > cursor]
        selected = visible[: limit + 1]
        items = selected[:limit]
        next_cursor = items[-1].slug if len(selected) > limit else None
        return ToolProductPage(items=items, next_cursor=next_cursor)

