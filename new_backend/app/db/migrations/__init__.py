"""Stable public imports for core and component SQLite migrations."""

from .common import add_column_if_missing, migrate_component_database
from .core import SCHEMA_VERSION, MIGRATIONS, migrate_core_database
from .components import (
    migrate_catalog_schema,
    migrate_identity_policy_schema,
    migrate_internal_tool_auth_schema,
    migrate_mcp_capacity_schema,
    migrate_mcp_tool_calls_schema,
    migrate_oauth_schema,
    migrate_pdf_capacity_schema,
    migrate_tool_run_schema,
)

__all__ = [
    "SCHEMA_VERSION", "MIGRATIONS", "add_column_if_missing", "migrate_core_database",
    "migrate_component_database", "migrate_catalog_schema", "migrate_identity_policy_schema",
    "migrate_internal_tool_auth_schema", "migrate_mcp_capacity_schema",
    "migrate_mcp_tool_calls_schema", "migrate_oauth_schema", "migrate_pdf_capacity_schema",
    "migrate_tool_run_schema",
]
