"""
app/database/db.py
SQLite helpers — backwards-compatible with the original events table schema.
Adds: stage, source, run_id columns (nullable so old rows still work).
"""
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = Path("data/events.db")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Schema versioning
#
# The database version is tracked with SQLite's native `PRAGMA user_version`
# (a 0 integer on a fresh DB). init_db() runs every pending migration in order
# and bumps the version, so upgrades are ordered, tracked, and idempotent —
# safe to call repeatedly and safe on a pre-existing DB created before
# versioning existed (it will be detected as v0 and migrated up to current).
#
# To evolve the schema: add a migration function and append it to MIGRATIONS
# with the next version number. Never edit a released migration in place.
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1  # latest version defined below in MIGRATIONS

BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT,
    date        TEXT,
    time_start  TEXT,
    time_end    TEXT,
    venue       TEXT,
    performer   TEXT,
    url         TEXT,
    stage       TEXT,
    source      TEXT,
    run_id      TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT UNIQUE,
    started_at  TEXT,
    events_saved INTEGER DEFAULT 0
);
"""


def get_connection(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def get_schema_version(path: Path = DB_PATH) -> int:
    """Return the database's current schema version (0 on a fresh/unversioned DB)."""
    conn = get_connection(path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def _add_column_if_missing(conn: sqlite3.Connection, table: str, col: str, coltype: str) -> None:
    """Additively add a column, ignoring the error if it already exists."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
        logger.info("Added column '%s.%s'", table, col)
    except sqlite3.OperationalError:
        pass  # column already exists


def _migration_1(conn: sqlite3.Connection) -> None:
    """
    v0 -> v1: baseline schema.

    Creates the events + runs tables and ensures the events table has the
    stage/source/run_id columns. Written to also upgrade a pre-versioning DB
    that already has some of these — every statement is idempotent.
    """
    conn.executescript(BASE_SCHEMA)
    for col, coltype in [("stage", "TEXT"), ("source", "TEXT"), ("run_id", "TEXT")]:
        _add_column_if_missing(conn, "events", col, coltype)


# Ordered list of (target_version, migration_fn). Append new migrations here.
MIGRATIONS: list[tuple[int, "callable"]] = [
    (1, _migration_1),
]


def init_db(path: Path = DB_PATH) -> None:
    """
    Bring the database up to SCHEMA_VERSION by running any pending migrations.
    Safe to call repeatedly and safe on a pre-versioning DB (detected as v0).
    """
    conn = get_connection(path)
    try:
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        for target, migrate in MIGRATIONS:
            if current < target:
                logger.info("Applying schema migration v%d -> v%d", current, target)
                migrate(conn)
                # PRAGMA cannot be parameterised; target is a trusted int constant.
                conn.execute(f"PRAGMA user_version = {int(target)}")
                conn.commit()
                current = target
        if current != SCHEMA_VERSION:
            logger.warning(
                "DB version %d does not match SCHEMA_VERSION %d after migration",
                current, SCHEMA_VERSION,
            )
        else:
            logger.info("DB schema at version %d", current)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Run tracking
# ---------------------------------------------------------------------------

def record_run(run_id: str, events_saved: int, path: Path = DB_PATH) -> None:
    conn = get_connection(path)
    conn.execute(
        "INSERT OR REPLACE INTO runs (run_id, started_at, events_saved) VALUES (?, ?, ?)",
        (run_id, datetime.now().isoformat(), events_saved),
    )
    conn.commit()
    conn.close()


def get_last_run_id(path: Path = DB_PATH) -> Optional[str]:
    conn = get_connection(path)
    row = conn.execute(
        "SELECT run_id FROM runs ORDER BY id DESC LIMIT 1 OFFSET 1"
    ).fetchone()
    conn.close()
    return row["run_id"] if row else None


# ---------------------------------------------------------------------------
# Events — read
# ---------------------------------------------------------------------------

def load_events(run_id: Optional[str] = None, path: Path = DB_PATH) -> list[dict]:
    conn = get_connection(path)
    if run_id:
        rows = conn.execute(
            "SELECT * FROM events WHERE run_id = ? ORDER BY date, time_start",
            (run_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM events ORDER BY date, time_start"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def load_all_events(path: Path = DB_PATH) -> list[dict]:
    return load_events(path=path)


# ---------------------------------------------------------------------------
# Events — write
# ---------------------------------------------------------------------------

def save_events(events: list[dict], run_id: str, path: Path = DB_PATH) -> int:
    """
    Insert events for this run. Does NOT delete old events so history is kept.
    Returns count saved.
    """
    if not events:
        return 0
    conn = get_connection(path)
    saved = 0
    for ev in events:
        try:
            conn.execute(
                """INSERT INTO events
                   (name, date, time_start, time_end, venue, performer, url, stage, source, run_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ev.get("name"),
                    ev.get("date"),
                    ev.get("time_start") or ev.get("time"),
                    ev.get("time_end"),
                    ev.get("venue"),
                    ev.get("performer"),
                    ev.get("url"),
                    ev.get("stage"),
                    ev.get("source", "unknown"),
                    run_id,
                ),
            )
            saved += 1
        except Exception as exc:
            logger.warning("Failed to save event %s: %s", ev.get("name"), exc)
    conn.commit()
    conn.close()
    return saved
