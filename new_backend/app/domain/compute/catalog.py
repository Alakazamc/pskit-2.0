"""Server-controlled, immutable computation versions; no client credentials."""

from jsonschema import Draft202012Validator
from psycopg.types.json import Jsonb

from app.contracts.compute import ComputeServiceManifest
from app.domain.compute.common import payload_hash


class ComputeCatalog:
    def __init__(self, database):
        self.database = database

    def register(self, manifest: ComputeServiceManifest):
        """Import an approved manifest from server configuration, never from user input."""
        with self.database.transaction() as connection:
            for capability in manifest.capabilities:
                Draft202012Validator.check_schema(capability.input_schema)
                Draft202012Validator.check_schema(capability.output_schema)
                data = manifest.model_copy(update={"capabilities": [capability]}).model_dump(mode="json")
                digest = payload_hash(data)
                row = connection.execute(
                    "INSERT INTO compute_capability_versions VALUES (%s,%s,%s,%s,%s) "
                    "ON CONFLICT(capability_id,version) DO NOTHING RETURNING manifest_hash",
                    (capability.id, capability.version, manifest.service_id, Jsonb(data), digest),
                ).fetchone()
                if row is None:
                    stored = connection.execute(
                        "SELECT manifest_hash FROM compute_capability_versions "
                        "WHERE capability_id=%s AND version=%s", (capability.id, capability.version),
                    ).fetchone()
                    if stored[0] != digest:
                        raise ValueError("IMMUTABLE_VERSION")

    def get(self, user_id, capability_id, version, *, connection=None):
        if connection is None:
            with self.database.connection() as conn:
                return self.get(user_id, capability_id, version, connection=conn)
        row = connection.execute(
            "SELECT manifest_json FROM compute_capability_versions "
            "WHERE capability_id=%s AND version=%s", (capability_id, version),
        ).fetchone()
        if row is None:
            return None
        manifest = ComputeServiceManifest.model_validate(row[0])
        capability = manifest.capabilities[0]
        if capability.visibility != "published" or (
            capability.allowed_users is not None and user_id not in capability.allowed_users
        ):
            return None
        return manifest.service_id, capability

    def for_user(self, user_id):
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT capability_id,version FROM compute_capability_versions "
                "ORDER BY capability_id,version"
            ).fetchall()
            return [capability for cid, version in rows
                    if (found := self.get(user_id, cid, version, connection=connection))
                    for capability in [found[1]]]
