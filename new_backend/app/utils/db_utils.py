"""Database query optimization utilities."""

from typing import List, Dict, Any


def batch_fetch_by_ids(
    db,
    table: str,
    id_column: str,
    ids: List[str],
    columns: str = "*"
) -> Dict[str, Any]:
    """Fetch multiple rows by IDs in a single query.

    Args:
        db: Database connection
        table: Table name
        id_column: ID column name
        ids: List of IDs to fetch
        columns: Columns to select (default: all)

    Returns:
        Dictionary mapping ID to row data
    """
    if not ids:
        return {}

    placeholders = ",".join("?" * len(ids))
    query = f"SELECT {columns} FROM {table} WHERE {id_column} IN ({placeholders})"
    rows = db.execute(query, tuple(ids)).fetchall()

    return {row[id_column]: dict(row) for row in rows}


def fetch_with_related(
    db,
    main_query: str,
    related_fetchers: Dict[str, callable],
    params: tuple = ()
) -> List[Dict[str, Any]]:
    """Fetch main records and eagerly load related data.

    Args:
        db: Database connection
        main_query: SQL query for main records
        related_fetchers: Dict of {field_name: fetcher_function}
        params: Query parameters

    Returns:
        List of records with related data attached

    Example:
        records = fetch_with_related(
            db,
            "SELECT * FROM agent_jobs WHERE status='running'",
            {
                'user': lambda ids: batch_fetch_by_ids(db, 'users', 'id', ids),
                'run': lambda ids: batch_fetch_by_ids(db, 'agent_runs', 'id', ids)
            }
        )
    """
    rows = db.execute(main_query, params).fetchall()
    if not rows:
        return []

    records = [dict(row) for row in rows]

    # Eagerly load related data for all records
    for field_name, fetcher in related_fetchers.items():
        # Collect all related IDs
        id_column = f"{field_name}_id"
        related_ids = [r[id_column] for r in records if r.get(id_column)]

        if related_ids:
            related_data = fetcher(list(set(related_ids)))
            # Attach related data to each record
            for record in records:
                related_id = record.get(id_column)
                if related_id and related_id in related_data:
                    record[field_name] = related_data[related_id]

    return records


class QueryBuilder:
    """Simple query builder to avoid SQL injection and improve readability."""

    def __init__(self, table: str):
        self.table = table
        self.select_cols = ["*"]
        self.where_conditions = []
        self.where_params = []
        self.joins = []
        self.order_by = []
        self.limit_val = None
        self.offset_val = None

    def select(self, *columns: str) -> "QueryBuilder":
        """Specify columns to select."""
        self.select_cols = list(columns)
        return self

    def where(self, condition: str, *params) -> "QueryBuilder":
        """Add WHERE condition."""
        self.where_conditions.append(condition)
        self.where_params.extend(params)
        return self

    def join(self, join_clause: str) -> "QueryBuilder":
        """Add JOIN clause."""
        self.joins.append(join_clause)
        return self

    def order(self, column: str, desc: bool = False) -> "QueryBuilder":
        """Add ORDER BY clause."""
        direction = "DESC" if desc else "ASC"
        self.order_by.append(f"{column} {direction}")
        return self

    def limit(self, limit: int) -> "QueryBuilder":
        """Add LIMIT clause."""
        self.limit_val = limit
        return self

    def offset(self, offset: int) -> "QueryBuilder":
        """Add OFFSET clause."""
        self.offset_val = offset
        return self

    def build(self) -> tuple[str, tuple]:
        """Build the final query and parameters."""
        query_parts = [
            f"SELECT {', '.join(self.select_cols)}",
            f"FROM {self.table}"
        ]

        if self.joins:
            query_parts.extend(self.joins)

        if self.where_conditions:
            query_parts.append(f"WHERE {' AND '.join(self.where_conditions)}")

        if self.order_by:
            query_parts.append(f"ORDER BY {', '.join(self.order_by)}")

        if self.limit_val is not None:
            query_parts.append(f"LIMIT {self.limit_val}")

        if self.offset_val is not None:
            query_parts.append(f"OFFSET {self.offset_val}")

        return " ".join(query_parts), tuple(self.where_params)

    def execute(self, db):
        """Build and execute the query."""
        query, params = self.build()
        return db.execute(query, params)


# Example usage in af3.py to avoid N+1 queries:
"""
# ❌ Bad: N+1 query
def get_jobs_with_users(self):
    jobs = self.db.execute("SELECT * FROM agent_jobs").fetchall()
    for job in jobs:
        user = self.db.execute(
            "SELECT * FROM users WHERE id=?", (job["user_id"],)
        ).fetchone()
        job["user"] = user

# ✅ Good: Single JOIN query
def get_jobs_with_users(self):
    return (
        QueryBuilder("agent_jobs")
        .select("j.*", "u.email", "u.role")
        .join("LEFT JOIN users u ON j.user_id = u.id")
        .where("j.status IN (?, ?)", "queued", "running")
        .execute(self.db)
        .fetchall()
    )

# ✅ Also good: Batch fetch
def get_jobs_with_users_batch(self):
    jobs = self.db.execute("SELECT * FROM agent_jobs").fetchall()
    user_ids = [j["user_id"] for j in jobs]
    users = batch_fetch_by_ids(self.db, "users", "id", user_ids)
    for job in jobs:
        job["user"] = users.get(job["user_id"])
    return jobs
"""
