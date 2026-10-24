"""SQLite persistence for rooms. Free, embedded, and fast enough to be invisible.

Why SQLite and not a hosted database: rooms are mutated by a single process
behind an asyncio lock (DEPLOY.md mandates `--workers 1`), so there is no
concurrent-writer problem for a client/server database to solve. A local file in
WAL mode commits in ~0.1 ms with no network hop.

How writes stay off the hot path:

  * Nothing is written while handling a message. `Persistence.mark(room)` only
    adds a room code to a dirty set, and a background task flushes every
    `interval` seconds -- so chat spam costs one write per interval, not per
    keystroke. Round transitions call `mark(room, urgent=True)` to flush at the
    next tick instead of waiting for a full interval.
  * The snapshot is JSON-encoded *in the event loop* (see `_encode`): the dict
    from `room_to_dict` aliases live game state, and only a synchronous
    function is guaranteed not to interleave with other coroutines.
  * Compression and the actual sqlite3 call run in `asyncio.to_thread`, working
    on an immutable string, so the loop never blocks on disk.

Snapshots are zlib-compressed (level 1: ~10x smaller for a few hundred
microseconds). Set ELECTION_DB=off to disable persistence entirely.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
import time
import zlib
from pathlib import Path

from .serialize import SCHEMA_VERSION, room_from_dict, room_to_dict

log = logging.getLogger("election.store")

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "election.db"
DISABLED = {"", "off", "none", "0", "no", "false", "disabled"}
FLUSH_INTERVAL = 2.0
SNAPSHOT_TTL = 24 * 3600      # snapshots older than this are dropped on startup

SCHEMA = """
CREATE TABLE IF NOT EXISTS rooms (
    code    TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    status  TEXT    NOT NULL,
    updated REAL    NOT NULL,
    data    BLOB    NOT NULL      -- zlib(json(room_to_dict(room)))
);
CREATE INDEX IF NOT EXISTS rooms_updated ON rooms (updated);
"""

# Durability is traded for speed deliberately: with WAL + synchronous=NORMAL a
# crash can lose the last couple of seconds of play, never the database file.
PRAGMAS = (
    ("journal_mode", "WAL"),
    ("synchronous", "NORMAL"),
    ("temp_store", "MEMORY"),
    ("mmap_size", 1 << 26),      # 64 MB
    ("cache_size", -8000),       # 8 MB page cache
    ("busy_timeout", 5000),
)


def db_path_from_env() -> Path | None:
    """None when persistence is switched off."""
    raw = os.environ.get("ELECTION_DB")
    if raw is None:
        return DEFAULT_DB
    return None if raw.strip().lower() in DISABLED else Path(raw).expanduser()


class Store:
    """Blocking SQLite access. Every method is safe to call from a worker thread."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    def open(self) -> "Store":
        if self.path.parent and str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        for name, value in PRAGMAS:
            conn.execute(f"PRAGMA {name}={value}")
        conn.executescript(SCHEMA)
        self._conn = conn
        return self

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                finally:
                    self._conn.close()
                    self._conn = None

    def save(self, rows: list[tuple[str, str, str]]) -> None:
        """rows: (code, status, json_text). Compresses here, off the event loop."""
        if not rows or self._conn is None:
            return
        now = time.time()
        payload = [(code, SCHEMA_VERSION, status, now, zlib.compress(text.encode("utf-8"), 1))
                   for code, status, text in rows]
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.executemany(
                    "INSERT INTO rooms (code, version, status, updated, data) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(code) DO UPDATE SET version=excluded.version, status=excluded.status, "
                    "updated=excluded.updated, data=excluded.data", payload)
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def delete(self, codes: list[str]) -> None:
        if not codes or self._conn is None:
            return
        with self._lock:
            self._conn.executemany("DELETE FROM rooms WHERE code = ?", [(c,) for c in codes])

    def load_all(self, ttl: float = SNAPSHOT_TTL) -> list[dict]:
        """Every snapshot still worth restoring. Unreadable rows are dropped, not fatal."""
        if self._conn is None:
            return []
        cutoff = time.time() - ttl
        with self._lock:
            self._conn.execute("DELETE FROM rooms WHERE updated < ? OR version != ?", (cutoff, SCHEMA_VERSION))
            rows = self._conn.execute("SELECT code, data FROM rooms ORDER BY updated").fetchall()
        out = []
        for code, blob in rows:
            try:
                out.append(json.loads(zlib.decompress(blob)))
            except Exception:
                log.warning("dropping unreadable snapshot for room %s", code)
        return out

    def count(self) -> int:
        if self._conn is None:
            return 0
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0]


class Persistence:
    """Write-behind snapshots: mark rooms dirty, a background task flushes them."""

    def __init__(self, store: Store | None, interval: float = FLUSH_INTERVAL):
        self.store = store
        self.interval = interval
        self._dirty: set[str] = set()
        self._gone: set[str] = set()
        self._urgent = False
        self._rooms: dict[str, object] = {}
        self._task: asyncio.Task | None = None

    @property
    def enabled(self) -> bool:
        return self.store is not None

    def bind(self, rooms: dict) -> None:
        """Point at the RoomManager's live room dict."""
        self._rooms = rooms

    def mark(self, room, urgent: bool = False) -> None:
        if not self.enabled:
            return
        self._dirty.add(room.code)
        self._gone.discard(room.code)
        self._urgent = self._urgent or urgent

    def forget(self, code: str) -> None:
        if not self.enabled:
            return
        self._dirty.discard(code)
        self._gone.add(code)

    def _encode(self) -> tuple[list[tuple[str, str, str]], list[str]]:
        """Runs in the event loop, synchronously: see the module docstring."""
        rows = []
        for code in sorted(self._dirty):
            room = self._rooms.get(code)
            if room is None:
                self._gone.add(code)
                continue
            try:
                rows.append((code, room.status, json.dumps(room_to_dict(room), separators=(",", ":"))))
            except Exception:
                log.exception("could not snapshot room %s", code)
        self._dirty.clear()
        gone, self._gone = sorted(self._gone), set()
        self._urgent = False
        return rows, gone

    async def flush(self) -> None:
        if not self.enabled or not (self._dirty or self._gone):
            return
        rows, gone = self._encode()

        def work():
            self.store.delete(gone)
            self.store.save(rows)

        try:
            await asyncio.to_thread(work)
        except Exception:
            log.exception("snapshot flush failed")

    def restore(self, room_cls, member_cls) -> list:
        """Rooms rebuilt from disk. Nobody is connected, so no timer is running."""
        if not self.enabled:
            return []
        rooms = []
        for data in self.store.load_all():
            try:
                room = room_from_dict(data, room_cls, member_cls)
            except Exception:
                log.warning("skipping room %s: snapshot no longer loadable", data.get("code"))
                continue
            if room.game and room.phase == "planning" and not room.game.finished:
                room.deadline = None   # restarted mid-round: the timer resumes when someone reconnects
            rooms.append(room)
        return rooms

    def start(self) -> None:
        if self.enabled and self._task is None:
            self._task = asyncio.create_task(self._run(), name="persistence-flush")

    async def _run(self) -> None:
        while True:
            try:
                for _ in range(int(self.interval * 10)):
                    await asyncio.sleep(0.1)
                    if self._urgent:
                        break
                await self.flush()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("persistence loop error")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        await self.flush()
        if self.store is not None:
            await asyncio.to_thread(self.store.close)


def build(path: Path | None = None) -> Persistence:
    """Persistence from ELECTION_DB (or `path`); a no-op object when disabled."""
    target = path if path is not None else db_path_from_env()
    if target is None:
        log.info("persistence disabled (ELECTION_DB)")
        return Persistence(None)
    return Persistence(Store(target).open())


__all__ = ["Store", "Persistence", "build", "db_path_from_env", "DEFAULT_DB"]
