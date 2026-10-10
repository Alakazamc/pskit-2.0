"""Guard stable backend imports while their implementations move into packages."""

from app.db import migrations
from app.domain import persistent_conversation


def test_persistent_conversation_store_is_exported_from_domain_package() -> None:
    """The public store import stays stable after its implementation is split."""
    assert hasattr(persistent_conversation, "__path__")
    assert persistent_conversation.PersistentConversationStore.__name__ == "PersistentConversationStore"


def test_database_migrations_keep_their_public_module_import() -> None:
    """The public migration functions remain available from their package."""
    assert hasattr(migrations, "__path__")
    assert callable(migrations.migrate_core_database)
    assert callable(migrations.migrate_component_database)
