"""Stable public imports for core and component SQLite migrations."""

from .common import add_column_if_missing, migrate_component_database
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
from .core import MIGRATIONS, SCHEMA_VERSION, migrate_core_database

__all__ = [
    "MIGRATIONS",
    "SCHEMA_VERSION",
    "add_column_if_missing",
    "migrate_catalog_schema",
    "migrate_component_database",
    "migrate_core_database",
    "migrate_identity_policy_schema",
    "migrate_internal_tool_auth_schema",
    "migrate_mcp_capacity_schema",
    "migrate_mcp_tool_calls_schema",
    "migrate_oauth_schema",
    "migrate_pdf_capacity_schema",
    "migrate_tool_run_schema",
]
