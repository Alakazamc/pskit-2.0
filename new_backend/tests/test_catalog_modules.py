"""Keep the catalog facade and existing callable boundaries after extraction."""

import inspect

from app.domain.catalog import CatalogStore, ContextNotFound, InvalidFileUpload, SkillManifest


def test_catalog_facade_delegates_existing_methods_without_signature_changes():
    from app.domain.catalog_files import FileCatalog
    from app.domain.catalog_skills import SkillRegistry
    from app.domain.catalog_tools import ToolCatalog

    assert all(issubclass(CatalogStore, item) for item in (FileCatalog, SkillRegistry, ToolCatalog))
    for name, component in (
        ("add_file", FileCatalog), ("file_bytes_for", FileCatalog),
        ("register_skill_version", SkillRegistry), ("set_skill_grants", SkillRegistry),
        ("refresh_mcp_tools", ToolCatalog), ("tool_allowed_for", ToolCatalog),
    ):
        assert getattr(CatalogStore, name) is getattr(component, name)
    assert str(inspect.signature(CatalogStore.file_bytes_for)) == (
        "(self, user_id: str, file_id: str) -> bytes | None"
    )
    assert str(inspect.signature(CatalogStore.set_skill_grants)).startswith(
        "(self, user_id: str, skill_ids: list[str], *, audit_callback=None"
    )


def test_catalog_shared_types_keep_the_public_import_identity():
    from app.domain import catalog_common

    assert ContextNotFound is catalog_common.ContextNotFound
    assert InvalidFileUpload is catalog_common.InvalidFileUpload
    assert SkillManifest is catalog_common.SkillManifest
