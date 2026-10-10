"""Seed a persisted pre-Profile route before constructing the test policy service."""

from app.contracts.admin import ModelDraft
from app.domain.admin.model_policy import ModelPolicy


def legacy_model_policy(store, catalog, *, alias: str, user_id: str) -> ModelPolicy:
    published = ModelDraft(expected_revision=0, reason="Persisted pre-upgrade fixture route",
                           allowed_user_ids=[user_id]).model_dump_json()
    with store.transaction():
        store.db.execute("INSERT INTO admin_models VALUES (?,?,'published',?,?)",
                         (alias, 0, published, published))
    return ModelPolicy(store, catalog, managed=True)
