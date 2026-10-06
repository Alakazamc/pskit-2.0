"""Durable owner-scoped events projected from generic compute jobs."""

from __future__ import annotations

import uuid
from typing import Any

from psycopg.types.json import Jsonb

from app.contracts.tool_products import ToolRunEvent
from app.domain.compute.common import payload_hash


class ComputeEvents:
    """Append and recover the ordered event stream for Tool Product runs."""

    def __init__(self, database) -> None:
        self.database = database

    @staticmethod
    def _event(row) -> ToolRunEvent:
        return ToolRunEvent(**dict(zip(
            ("run_id", "sequence", "event_id", "type", "data", "created_at"),
            row,
        )))

    def append(
        self,
        job_id: str,
        type: str,
        data: dict[str, Any],
        *,
        connection,
        key: str | None = None,
    ) -> ToolRunEvent | None:
        """Append once for a compute job attached to a Tool Product run."""
        run = connection.execute(
            "SELECT r.run_id FROM tool_product_run_steps s "
            "JOIN tool_product_runs r ON r.run_id=s.run_id "
            "WHERE s.compute_job_id=%s FOR UPDATE OF r",
            (job_id,),
        ).fetchone()
        if run is None:
            return None
        run_id = run[0]
        identity = key or uuid.uuid4().hex
        identity_job_id = None if key and key.startswith("run:") else job_id
        event_id = f"event-{payload_hash({
            'run_id': run_id,
            'job_id': identity_job_id,
            'type': type,
            'key': identity,
        })}"
        prior = connection.execute(
            "SELECT run_id,sequence,event_id,type,data_json,created_at "
            "FROM tool_run_events WHERE event_id=%s",
            (event_id,),
        ).fetchone()
        if prior is not None:
            return self._event(prior)
        sequence = connection.execute(
            "SELECT COALESCE(MAX(sequence),0)+1 FROM tool_run_events WHERE run_id=%s",
            (run_id,),
        ).fetchone()[0]
        row = connection.execute(
            "INSERT INTO tool_run_events "
            "(run_id,sequence,event_id,type,data_json) VALUES (%s,%s,%s,%s,%s) "
            "RETURNING run_id,sequence,event_id,type,data_json,created_at",
            (run_id, sequence, event_id, type, Jsonb(data)),
        ).fetchone()
        return self._event(row)

    def after(
        self,
        run_id: str,
        user_id: str,
        cursor: int,
        limit: int,
    ) -> list[ToolRunEvent]:
        """Return an owned, bounded page strictly after the sequence cursor."""
        if cursor < 0:
            raise ValueError("INVALID_EVENT_CURSOR")
        if limit < 1 or limit > 100:
            raise ValueError("INVALID_PAGE_LIMIT")
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT e.run_id,e.sequence,e.event_id,e.type,e.data_json,e.created_at "
                "FROM tool_run_events e JOIN tool_product_runs r ON r.run_id=e.run_id "
                "WHERE e.run_id=%s AND r.user_id=%s AND e.sequence>%s "
                "ORDER BY e.sequence LIMIT %s",
                (run_id, user_id, cursor, limit),
            ).fetchall()
        return [self._event(row) for row in rows]
