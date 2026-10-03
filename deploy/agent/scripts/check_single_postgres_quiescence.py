"""Read-only cutover gate for Agent runs, AF3 jobs, and receiver journal."""

from __future__ import annotations

import argparse
import sqlite3
from contextlib import closing
from pathlib import Path

TERMINAL = ("completed", "failed", "cancelled")


def _read_only(path: Path) -> sqlite3.Connection:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError("Required SQLite source is missing or unsafe")
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
        connection.close()
        raise ValueError("SQLite integrity check failed")
    return connection


def check_quiescence(
    agent_db: Path, journal_db: Path, spool_jobs_dir: Path,
) -> tuple[int, int, int, int]:
    """Return count-only evidence or reject any unfinished work."""
    spool_jobs_dir = Path(spool_jobs_dir)
    if spool_jobs_dir.is_symlink() or not spool_jobs_dir.is_dir():
        raise FileNotFoundError("Receiver spool jobs directory is missing or unsafe")
    with closing(_read_only(agent_db)) as agent, closing(_read_only(journal_db)) as journal:
        active_runs = agent.execute(
            "SELECT count(*) FROM agent_runs WHERE status IS NULL OR status NOT IN (?, ?, ?)", TERMINAL,
        ).fetchone()[0]
        active_jobs = agent.execute(
            "SELECT count(*) FROM agent_jobs WHERE status IS NULL OR status NOT IN (?, ?, ?)", TERMINAL,
        ).fetchone()[0]
        unacked = journal.execute("SELECT count(*) FROM jobs").fetchone()[0]
    spool_entries = sum(1 for _ in spool_jobs_dir.iterdir())
    counts = (active_runs, active_jobs, unacked, spool_entries)
    if any(counts):
        raise RuntimeError(
            f"Cutover blocked: active runs={active_runs}, active jobs={active_jobs}, "
            f"unacked journal jobs={unacked}, spool entries={spool_entries}"
        )
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("agent_db", type=Path)
    parser.add_argument("receiver_journal", type=Path)
    parser.add_argument("receiver_spool_jobs_dir", type=Path)
    args = parser.parse_args()
    try:
        counts = check_quiescence(
            args.agent_db, args.receiver_journal, args.receiver_spool_jobs_dir,
        )
    except (FileNotFoundError, ValueError, RuntimeError, sqlite3.Error) as exc:
        parser.exit(2, f"Cutover blocked: {exc}\n")
    print(
        f"Cutover gate passed: runs={counts[0]}, jobs={counts[1]}, "
        f"journal={counts[2]}, spool={counts[3]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
