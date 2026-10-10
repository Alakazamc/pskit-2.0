"""Bounded read-only observations of receiver journals and retained spool state."""

import os
import sqlite3
import stat
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

KEYS = ("active", "unknown", "journal_pending", "outbox_pending")
_COLUMNS = [("job_id", "TEXT", 0, None, 1), ("state", "TEXT", 1, None, 0),
            ("grant_json", "TEXT", 1, None, 0), ("payload_json", "TEXT", 0, None, 0),
            ("pending_json", "TEXT", 0, None, 0), ("seq", "INTEGER", 1, "0", 0),
            ("progress", "INTEGER", 1, "0", 0)]


class LiveActivity:
    """Explicit server-configured lanes; a started/failed poll is never clean."""

    def __init__(self, lanes):
        if not lanes or any(type(enabled) is not bool for enabled in lanes.values()):
            raise ValueError("ACTIVITY_CONFIG_INVALID")
        self._lock, self._generation = threading.Lock(), 0
        self._lanes = {name: {"enabled": enabled, "ready": not enabled, "busy": False, "status": "idle"}
                       for name, enabled in lanes.items()}

    def begin(self, lane):
        with self._lock:
            value = self._lanes[lane]
            if not value["enabled"] or value["busy"]:
                raise ValueError("ACTIVITY_LANE_INVALID")
            value["busy"] = True
            self._generation += 1

    def finish(self, lane, status):
        with self._lock:
            value = self._lanes[lane]
            value.update(ready=True, busy=False, status=status)
            self._generation += 1

    def snapshot(self):
        with self._lock:
            counts = dict.fromkeys(KEYS, 0)
            for value in self._lanes.values():
                if value["busy"]:
                    counts["active"] += 1
                if not value["ready"] or value["busy"] or value["status"] not in {"idle", "acknowledged", "pending"}:
                    counts["unknown"] += 1
                if value["status"] == "pending":
                    counts["active"] += 1
            return self._generation, counts


class ObservedReceiver:
    """Preserve the existing worker interface while fencing its live observation."""

    def __init__(self, receiver, live, lane):
        self.receiver, self.live, self.lane = receiver, live, lane

    def __getattr__(self, name):
        return getattr(self.receiver, name)

    async def run_once(self):
        self.live.begin(self.lane)
        try:
            result = await self.receiver.run_once()
        except BaseException:
            self.live.finish(self.lane, "unknown")
            raise
        self.live.finish(self.lane, result.status)
        return result


def _checked(path, *, directory=False):
    path = Path(path).absolute()
    for component in (path, *path.parents):
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ValueError("ACTIVITY_PATH_INVALID")
    info = path.stat()
    if (not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or not info.st_mode & 0o444 or directory and not info.st_mode & 0o111):
        raise ValueError("ACTIVITY_PATH_INVALID")
    return info


@contextmanager
def _directory(path):
    """Keep a no-follow directory identity while SQLite opens its WAL siblings."""
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in Path(path).absolute().parts[1:]:
            following = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
        yield descriptor
    finally:
        os.close(descriptor)


class ActivityObserver:
    def __init__(self, journals, spool, live, *, max_entries=4096, timeout_seconds=1):
        if len(journals) != 3 or len(set(map(str, journals))) != 3 or not 1 <= max_entries <= 4096:
            raise ValueError("ACTIVITY_CONFIG_INVALID")
        if not 0 < timeout_seconds <= 2:
            raise ValueError("ACTIVITY_CONFIG_INVALID")
        self.journals = tuple(Path(path).absolute() for path in journals)
        self.spool, self.live = Path(spool).absolute(), live
        self.max_entries, self.timeout = max_entries, timeout_seconds

    def _signature(self):
        result = []
        for path in self.journals:
            for suffix in ("", "-wal", "-shm"):
                name = Path(str(path) + suffix)
                info = _checked(name)
                signature = (str(name), info.st_dev, info.st_ino, info.st_size, info.st_mode)
                # WAL readers may update shared-memory read marks. The durable
                # database/WAL and sidecar identity must remain unchanged.
                result.append(signature if suffix == "-shm" else
                              (*signature, info.st_mtime_ns, info.st_ctime_ns))
        info = _checked(self.spool, directory=True)
        result.append((str(self.spool), info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns))
        parents = {parent for path in (*self.journals, self.spool) for parent in path.parents}
        for parent in sorted(parents):
            info = _checked(parent, directory=True)
            result.append((str(parent), info.st_dev, info.st_ino, info.st_mode, info.st_ctime_ns))
        return tuple(result)

    def _journal(self, path):
        with _directory(path.parent) as parent:
            # SQLite mode=ro can create missing WAL/SHM files. Requiring both
            # existing regular sidecars avoids that write and includes live WAL data.
            for suffix in ("", "-wal", "-shm"):
                name = path.name + suffix
                info = _checked(path.with_name(name))
                descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    opened = os.fstat(descriptor)
                    if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                        raise ValueError("ACTIVITY_PATH_INVALID")
                    if not suffix:
                        header = os.read(descriptor, 20)
                        if header[:16] != b"SQLite format 3\x00" or header[18:20] != b"\x02\x02":
                            raise ValueError("ACTIVITY_JOURNAL_INVALID")
                finally:
                    os.close(descriptor)
            # A pathname parent may be renamed/replaced after validation. The
            # descriptor path still selects the configured directory, and parent
            # ctime signatures make any transient replacement an unknown snapshot.
            pinned = Path("/proc/self/fd") / str(parent) / path.name
            connection = sqlite3.connect(pinned.as_uri() + "?mode=ro", uri=True, timeout=self.timeout)
            return self._rows(connection)

    def _rows(self, connection):
        deadline = time.monotonic() + self.timeout
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            connection.execute("BEGIN")
            tables = connection.execute("SELECT name,type FROM sqlite_master WHERE type IN ('table','view','trigger') LIMIT 2").fetchall()
            if tables != [("executions", "table")]:
                raise ValueError("ACTIVITY_SCHEMA_UNKNOWN")
            columns = connection.execute("PRAGMA table_info(executions)").fetchall()
            if [(item[1], item[2], item[3], item[4], item[5]) for item in columns] != _COLUMNS:
                raise ValueError("ACTIVITY_SCHEMA_UNKNOWN")
            if connection.execute("PRAGMA quick_check(1)").fetchone() != ("ok",):
                raise ValueError("ACTIVITY_JOURNAL_INVALID")
            rows = connection.execute("SELECT state,payload_json IS NOT NULL FROM executions LIMIT ?",
                                      (self.max_entries + 1,)).fetchall()
            if len(rows) > self.max_entries:
                raise ValueError("ACTIVITY_SCAN_LIMIT")
            counts = dict.fromkeys(KEYS, 0)
            counts["journal_pending"] = len(rows)
            for state, payload in rows:
                if state in {"executing", "waiting"}:
                    counts["active"] += 1
                if state == "outbox" or payload:
                    counts["outbox_pending"] += 1
                if state not in {"waiting", "outbox"} or state == "outbox" and not payload:
                    counts["unknown"] += 1
            return counts
        finally:
            connection.close()

    def _spool(self):
        _checked(self.spool, directory=True)
        counts, signature = dict.fromkeys(KEYS, 0), []
        deadline = time.monotonic() + self.timeout
        with os.scandir(self.spool) as iterator:
            for index, entry in enumerate(iterator):
                if index >= self.max_entries or time.monotonic() > deadline:
                    raise ValueError("ACTIVITY_SCAN_LIMIT")
                path = Path(entry.path)
                info = _checked(path, directory=True)
                signature.append((entry.name, info.st_ino, info.st_mtime_ns, info.st_ctime_ns))
                if str(uuid.UUID(entry.name)) != entry.name:
                    raise ValueError("ACTIVITY_SPOOL_UNKNOWN")
                # Presence of retained output is pending delivery even when its
                # journal has been acknowledged. Payload contents are never read.
                outcome = path / "outcome.json"
                if os.path.lexists(outcome):
                    _checked(outcome)
                    counts["outbox_pending"] += 1
                else:
                    counts["active"] += 1
                    if not os.path.lexists(path / "input.json"):
                        counts["unknown"] += 1
                    else:
                        _checked(path / "input.json")
        return counts, tuple(sorted(signature))

    def observe(self):
        generation, counts = self.live.snapshot()
        before = None
        try:
            before = self._signature()
        except (OSError, ValueError):
            counts["unknown"] += 1
        for path in self.journals:
            try:
                for key, value in self._journal(path).items():
                    counts[key] += value
            except (OSError, ValueError, sqlite3.Error):
                counts["unknown"] += 1
        try:
            spool_counts, spool_signature = self._spool()
            for key, value in spool_counts.items():
                counts[key] += value
            if self._spool()[1] != spool_signature:
                counts["unknown"] += 1
        except (OSError, ValueError):
            counts["unknown"] += 1
        try:
            if before is None or self._signature() != before or self.live.snapshot()[0] != generation:
                counts["unknown"] += 1
        except (OSError, ValueError):
            counts["unknown"] += 1
        return {"schema_version": "pskit.receiver-activity.v1", "observed_at": time.time(), "counts": counts}
