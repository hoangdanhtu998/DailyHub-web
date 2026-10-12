"""SQLite storage for the AI Personal Growth Hub.

The database lives in <project>/data/dailyhub.db (override with DAILYHUB_DATA).
Schema changes are applied as numbered migrations; the current version is kept
in the `meta` table, so existing databases keep working after an upgrade.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DAILYHUB_DATA", ROOT / "data"))
DB_FILE = DATA_DIR / "dailyhub.db"

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None
_db_path: Path | None = None

MIGRATIONS: dict[int, str] = {
    1: """
    CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS resources (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        url TEXT NOT NULL UNIQUE,
        author TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        topics TEXT NOT NULL DEFAULT '[]',
        source_id TEXT NOT NULL DEFAULT '',
        first_seen INTEGER NOT NULL,
        last_seen INTEGER NOT NULL,
        last_updated TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'active',
        metrics TEXT NOT NULL DEFAULT '{}',
        metrics_at INTEGER,
        score INTEGER,
        score_breakdown TEXT NOT NULL DEFAULT '{}',
        license TEXT NOT NULL DEFAULT '',
        install_hint TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_resources_kind ON resources(kind);
    CREATE TABLE IF NOT EXISTS resource_state (
        resource_id TEXT PRIMARY KEY REFERENCES resources(id) ON DELETE CASCADE,
        saved INTEGER NOT NULL DEFAULT 0,
        seen INTEGER NOT NULL DEFAULT 0,
        rating INTEGER,
        note TEXT NOT NULL DEFAULT '',
        updated INTEGER
    );
    CREATE TABLE IF NOT EXISTS sources (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        type TEXT NOT NULL,
        url TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        last_ok INTEGER,
        last_error TEXT NOT NULL DEFAULT '',
        ok_count INTEGER NOT NULL DEFAULT 0,
        fail_count INTEGER NOT NULL DEFAULT 0,
        last_count INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trigger TEXT NOT NULL,
        started INTEGER NOT NULL,
        finished INTEGER,
        status TEXT NOT NULL,
        stats TEXT NOT NULL DEFAULT '{}'
    );
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        resource_id TEXT,
        type TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT '',
        created INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS memories (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        title TEXT NOT NULL,
        body TEXT NOT NULL DEFAULT '',
        tags TEXT NOT NULL DEFAULT '[]',
        source TEXT NOT NULL DEFAULT '',
        confidence TEXT NOT NULL DEFAULT 'medium',
        valid_from TEXT NOT NULL DEFAULT '',
        review_at TEXT NOT NULL DEFAULT '',
        pinned INTEGER NOT NULL DEFAULT 0,
        stale INTEGER NOT NULL DEFAULT 0,
        created INTEGER NOT NULL,
        updated INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS prompts (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        goal TEXT NOT NULL DEFAULT '',
        body TEXT NOT NULL,
        variables TEXT NOT NULL DEFAULT '[]',
        models TEXT NOT NULL DEFAULT '[]',
        version INTEGER NOT NULL DEFAULT 1,
        tags TEXT NOT NULL DEFAULT '[]',
        result_note TEXT NOT NULL DEFAULT '',
        rating INTEGER,
        created INTEGER NOT NULL,
        updated INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS prompt_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        prompt_id TEXT NOT NULL REFERENCES prompts(id) ON DELETE CASCADE,
        version INTEGER NOT NULL,
        body TEXT NOT NULL,
        created INTEGER NOT NULL
    );
    """,
}


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def _migrate(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    current = int(row[0]) if row else 0
    for version in sorted(MIGRATIONS):
        if version <= current:
            continue
        conn.executescript(MIGRATIONS[version])
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)", (str(version),))
    conn.commit()


def open_db(path: Path | None = None) -> None:
    """Open (or reopen) the database. Tests pass a temporary path."""
    global _conn, _db_path
    with _lock:
        if _conn is not None:
            _conn.close()
        target = Path(path) if path else DB_FILE
        target.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(target), check_same_thread=False, timeout=10)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA foreign_keys = ON")
        _conn.execute("PRAGMA journal_mode = WAL")
        _migrate(_conn)
        _db_path = target


def conn() -> sqlite3.Connection:
    with _lock:  # the scheduler thread and HTTP threads may both arrive first
        if _conn is None:
            open_db()
        return _conn  # type: ignore[return-value]


@contextmanager
def tx():
    """Serialized write transaction. Commits on success, rolls back on error."""
    with _lock:
        c = conn()
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise


def query(sql: str, args: tuple = ()) -> list[dict]:
    """Read rows as dicts, under the same lock as writes (one shared connection)."""
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


def query_one(sql: str, args: tuple = ()) -> dict | None:
    rows = query(sql, args)
    return rows[0] if rows else None


def get_meta(key: str, default: str | None = None) -> str | None:
    with _lock:
        row = conn().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default


def set_meta(key: str, value: str) -> None:
    with tx() as c:
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, value))


def schema_version() -> int:
    return int(get_meta("schema_version", "0") or 0)
