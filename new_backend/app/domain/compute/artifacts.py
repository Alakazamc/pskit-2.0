"""Verified computation files stored in the existing owned PostgreSQL blob store."""

import hashlib
import re
from pathlib import PurePath

from app.contracts.catalog import ArtifactRef
from app.domain.compute.common import utcnow

MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_JOB_ARTIFACT_BYTES = 256 * 1024 * 1024
MAX_JOB_ARTIFACTS = 128


class ComputeArtifacts:
    def __init__(self, database):
        self.database = database

    @staticmethod
    def owned_id(job_id, source_id):
        return f"artifact-{job_id}-{source_id}"

    @staticmethod
    def validate_metadata(artifact):
        if (not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", artifact.id)
                or not artifact.name or len(artifact.name) > 255
                or PurePath(artifact.name).name != artifact.name
                or any(ord(char) < 32 for char in artifact.name)
                or "\\" in artifact.name or artifact.name in {".", ".."}
                or not artifact.kind or len(artifact.kind) > 200
                or artifact.size is None or not 0 <= artifact.size <= MAX_ARTIFACT_BYTES
                or not artifact.sha256 or not re.fullmatch(r"[0-9a-f]{64}", artifact.sha256)
                or not artifact.available):
            raise ValueError("INVALID_ARTIFACT_METADATA")

    @classmethod
    def put(cls, connection, user_id, job_id, artifact, raw):
        cls.validate_metadata(artifact)
        if len(raw) != artifact.size or hashlib.sha256(raw).hexdigest() != artifact.sha256:
            raise ValueError("INVALID_ARTIFACT_CONTENT")
        previous = connection.execute(
            "SELECT user_id,job_id,name,kind,size,sha256 FROM agent_artifact_blobs WHERE id=%s",
            (cls.owned_id(job_id, artifact.id),),
        ).fetchone()
        expected = (user_id, job_id, artifact.name, artifact.kind, artifact.size, artifact.sha256)
        if previous:
            if previous != expected:
                raise ValueError("ARTIFACT_CONFLICT")
            return artifact
        count, size = connection.execute(
            "SELECT COUNT(*),COALESCE(SUM(size),0) FROM agent_artifact_blobs WHERE job_id=%s",
            (job_id,),
        ).fetchone()
        if count >= MAX_JOB_ARTIFACTS or size + len(raw) > MAX_JOB_ARTIFACT_BYTES:
            raise ValueError("INVALID_ARTIFACT_STORAGE_LIMIT")
        connection.execute(
            "INSERT INTO agent_artifact_blobs "
            "(id,user_id,job_id,name,kind,size,sha256,content,created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (cls.owned_id(job_id, artifact.id), *expected, raw, utcnow().isoformat()),
        )
        return artifact

    @classmethod
    def validate_report(cls, connection, job_id, artifacts):
        for artifact in artifacts:
            if not artifact.available:
                continue
            row = connection.execute(
                "SELECT name,kind,size,sha256 FROM agent_artifact_blobs WHERE id=%s AND job_id=%s",
                (cls.owned_id(job_id, artifact.id), job_id),
            ).fetchone()
            if row != (artifact.name, artifact.kind, artifact.size, artifact.sha256):
                raise ValueError("ARTIFACT_NOT_COMMITTED")

    @classmethod
    def available_refs(cls, connection, job_id, artifacts):
        result = []
        for artifact in artifacts:
            owned_id = cls.owned_id(job_id, artifact.id)
            row = connection.execute(
                "SELECT name,kind,size,sha256 FROM agent_artifact_blobs WHERE id=%s AND job_id=%s",
                (owned_id, job_id),
            ).fetchone()
            available = artifact.available and row == (artifact.name, artifact.kind, artifact.size, artifact.sha256)
            result.append(artifact.model_copy(update={"id": owned_id if available else artifact.id,
                                                       "available": available}))
        return result

    def _rows(self, user_id, *, artifact_id=None, session_id=None, content=False):
        # A worker upload is staged; only a matching terminal report releases it.
        clauses, params = ["b.user_id=%s", "j.user_id=%s"], [user_id, user_id]
        if artifact_id is not None:
            clauses.append("b.id=%s")
            params.append(artifact_id)
        if session_id is not None:
            clauses.append("r.session_id=%s AND r.user_id=%s")
            params.extend([session_id, user_id])
        with self.database.connection() as connection:
            return connection.execute(
                "SELECT b.id,b.name,b.kind,b.size,b.sha256" + (",b.content" if content else "")
                + " FROM agent_artifact_blobs b JOIN agent_jobs j ON j.id=b.job_id "
                "JOIN compute_job_data d ON d.job_id=j.id LEFT JOIN agent_runs r ON r.id=j.run_id "
                "WHERE j.status IN ('completed','failed','cancelled') AND "
                + " AND ".join(clauses)
                + " AND EXISTS (SELECT 1 FROM jsonb_array_elements("
                "COALESCE(d.report_json->'artifacts','[]'::jsonb)) a WHERE "
                "('artifact-' || j.id || '-' || (a->>'id'))=b.id "
                "AND a->>'sha256'=b.sha256 AND a->>'name'=b.name "
                "AND a->>'kind'=b.kind AND a->>'size'=b.size::text AND a->>'available'='true') "
                "ORDER BY b.ordinal", tuple(params),
            ).fetchall()

    def list(self, user_id, session_id=None):
        return [ArtifactRef(id=row[0], name=row[1], kind=row[2], available=True,
                            size=row[3], sha256=row[4])
                for row in self._rows(user_id, session_id=session_id)]

    def read(self, user_id, artifact_id):
        rows = self._rows(user_id, artifact_id=artifact_id, content=True)
        return (rows[0][1], bytes(rows[0][5])) if rows else None
