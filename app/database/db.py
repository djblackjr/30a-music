"""
app/database/db.py
SQLite helpers — backwards-compatible with the original events table schema.
Adds: stage, source, run_id columns (nullable so old rows still work).
"""
import logging
import sqlite3
from datetime import date as _pydate
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

SCHEMA_VERSION = 6  # latest version defined below in MIGRATIONS

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


# Source-trust defaults used ONLY to backfill pre-existing rows during the v2
# migration. The authoritative live scorer is app/normalize/confidence.py (Phase 2);
# this is a conservative one-time default so no legacy row is left unscored.
_BACKFILL_SOURCE_TRUST = {
    "sowal":   0.9,
    "crawler": 0.9,
    "seed":    0.6,
}


def _migration_2(conn: sqlite3.Connection) -> None:
    """
    v1 -> v2: confidence fields + legacy data backfill.

    Purely additive:
      - adds events.confidence (REAL) and events.confidence_reason (TEXT)
      - backfills confidence for existing rows from source trust (NULLs only)
      - backfills run_id = 'legacy' for rows that predate run tracking

    No rows are deleted and no existing values are overwritten — every UPDATE is
    guarded by an `IS NULL` / empty check, so re-running is a no-op.
    """
    _add_column_if_missing(conn, "events", "confidence", "REAL")
    _add_column_if_missing(conn, "events", "confidence_reason", "TEXT")

    # Backfill confidence from source trust, image:* and ocr* handled by prefix.
    conn.execute(
        """
        UPDATE events
           SET confidence = CASE
                   WHEN source LIKE 'image:%' THEN 0.8
                   WHEN source LIKE 'ocr%'    THEN 0.5
                   WHEN source = 'sowal'      THEN 0.9
                   WHEN source = 'crawler'    THEN 0.9
                   WHEN source = 'seed'       THEN 0.6
                   ELSE 0.5
               END,
               confidence_reason = 'backfilled at v2 migration from source trust'
         WHERE confidence IS NULL
        """
    )

    # Guard rows that predate run tracking so every event belongs to a run.
    conn.execute(
        "UPDATE events SET run_id = 'legacy' WHERE run_id IS NULL OR run_id = ''"
    )


def _migration_3(conn: sqlite3.Connection) -> None:
    """
    v2 -> v3: source provenance.

    Purely additive:
      - adds provenance summary columns to events
        (source_count, verification_count, conflict_flag, conflict_reason)
      - creates the event_sources table (one row per observation)

    No rows are deleted; existing events simply have NULL provenance columns
    until the next run re-computes them.
    """
    for col, coltype in [
        ("source_count", "INTEGER"),
        ("verification_count", "INTEGER"),
        ("conflict_flag", "INTEGER"),
        ("conflict_reason", "TEXT"),
    ]:
        _add_column_if_missing(conn, "events", col, coltype)

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS event_sources (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id              INTEGER,
            source                TEXT,
            url                   TEXT,
            source_confidence     REAL,
            extraction_confidence REAL,
            confidence            REAL,
            observed_at           TEXT,
            checksum              TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_event_sources_event_id ON event_sources(event_id);
        """
    )


def _migration_4(conn: sqlite3.Connection) -> None:
    """
    v3 -> v4: rename event_sources -> event_observations.

    A row is an observation, not a source (one source produces many observations).
    Done before the name becomes public API. In-place rename via ALTER TABLE —
    all data is preserved, nothing is rebuilt or deleted. Defensive: only renames
    if the old table still exists and the new one does not.
    """
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    if "event_sources" in tables and "event_observations" not in tables:
        conn.execute("ALTER TABLE event_sources RENAME TO event_observations")

    # Point the index at the new name (index names don't auto-rename).
    conn.execute("DROP INDEX IF EXISTS idx_event_sources_event_id")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_event_observations_event_id "
        "ON event_observations(event_id)"
    )


def _migration_5(conn: sqlite3.Connection) -> None:
    """
    v4 -> v5: add event_observations.observation_type.

    How the observation was obtained — website / image / ocr / api / manual /
    social / calendar — distinct from `source` (who/where). Additive; existing
    rows are backfilled from their source.
    """
    _add_column_if_missing(conn, "event_observations", "observation_type", "TEXT")
    conn.execute(
        """
        UPDATE event_observations SET observation_type = CASE
                WHEN source LIKE 'image:%'            THEN 'image'
                WHEN source LIKE 'ocr%'               THEN 'ocr'
                WHEN source IN ('instagram','facebook') THEN 'social'
                WHEN source = 'seed'                  THEN 'manual'
                ELSE 'website'
            END
         WHERE observation_type IS NULL
        """
    )


def _migration_6(conn: sqlite3.Connection) -> None:
    """
    v5 -> v6: cross-run observation accumulation.

    Previously each run inserted its OWN events row per identity, so the same
    event observed by two sources in two runs became two rows with one
    observation each — they never corroborated. Now there is ONE canonical event
    per identity and observations accumulate onto it.

      - events.identity_key (performer|venue|date), made UNIQUE
      - event_observations gains time_start/time_end/stage: what THAT observation
        asserted, so conflicts can be detected between observations from
        different runs
      - duplicate event rows for the same identity are collapsed into one
        (observations re-pointed, duplicates removed), then aggregates recomputed

    Additive to the schema; the collapse only merges rows that were already
    duplicates of the same event. No observation is lost.
    """
    _add_column_if_missing(conn, "events", "identity_key", "TEXT")
    for col in ("time_start", "time_end", "stage"):
        _add_column_if_missing(conn, "event_observations", col, "TEXT")

    # Backfill the identity key.
    conn.execute(
        """
        UPDATE events SET identity_key =
            lower(trim(coalesce(performer,''))) || '|' ||
            lower(trim(coalesce(venue,'')))     || '|' ||
            trim(coalesce(date,''))
         WHERE identity_key IS NULL
        """
    )

    # Backfill what each observation asserted from the event it was attached to
    # (pre-v6 an event row carried exactly one run's assertion).
    conn.execute(
        """
        UPDATE event_observations SET
            time_start = (SELECT time_start FROM events e WHERE e.id = event_observations.event_id),
            time_end   = (SELECT time_end   FROM events e WHERE e.id = event_observations.event_id),
            stage      = (SELECT stage      FROM events e WHERE e.id = event_observations.event_id)
         WHERE time_start IS NULL AND time_end IS NULL AND stage IS NULL
        """
    )

    # Collapse duplicate identities: keep the earliest row, re-point observations.
    dupes = conn.execute(
        "SELECT identity_key, MIN(id) AS keep FROM events "
        "GROUP BY identity_key HAVING COUNT(*) > 1"
    ).fetchall()
    for d in dupes:
        conn.execute(
            "UPDATE event_observations SET event_id = ? "
            "WHERE event_id IN (SELECT id FROM events WHERE identity_key = ? AND id != ?)",
            (d["keep"], d["identity_key"], d["keep"]),
        )
        conn.execute(
            "DELETE FROM events WHERE identity_key = ? AND id != ?",
            (d["identity_key"], d["keep"]),
        )
    logger.info("Collapsed %d duplicate identities into canonical events", len(dupes))

    # Drop repeat observations of identical content from the same source.
    conn.execute(
        "DELETE FROM event_observations WHERE id NOT IN "
        "(SELECT MIN(id) FROM event_observations GROUP BY event_id, source, checksum)"
    )

    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_identity ON events(identity_key)"
    )

    # Recompute aggregates now that observations have accumulated.
    from app.normalize.provenance import aggregate_observations

    for row in conn.execute("SELECT id FROM events").fetchall():
        obs = [dict(r) for r in conn.execute(
            "SELECT * FROM event_observations WHERE event_id = ?", (row["id"],)
        ).fetchall()]
        if not obs:
            continue
        agg = aggregate_observations(obs)
        conn.execute(
            """UPDATE events SET confidence = ?, confidence_reason = ?, source_count = ?,
                                 verification_count = ?, conflict_flag = ?, conflict_reason = ?
                WHERE id = ?""",
            (agg["confidence"], agg["confidence_reason"], agg["source_count"],
             agg["verification_count"], agg["conflict_flag"], agg["conflict_reason"], row["id"]),
        )


# Ordered list of (target_version, migration_fn). Append new migrations here.
MIGRATIONS: list[tuple[int, "callable"]] = [
    (1, _migration_1),
    (2, _migration_2),
    (3, _migration_3),
    (4, _migration_4),
    (5, _migration_5),
    (6, _migration_6),
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


def load_current_events(path: Path = DB_PATH) -> list[dict]:
    """
    The current picture of known events: the union across all runs, keeping the
    most recent version of each identity (performer + venue + date). This lets a
    new pipeline run add/update events without dropping events it didn't re-observe
    (e.g. the migrated legacy set persists until superseded).
    """
    best: dict[tuple, dict] = {}
    for e in load_events(path=path):
        key = (
            (e.get("performer") or "").strip().lower(),
            (e.get("venue") or "").strip().lower(),
            (e.get("date") or "").strip(),
        )
        cur = best.get(key)
        rank = (e.get("run_id") or "", e.get("id") or 0)
        if cur is None or rank > (cur.get("run_id") or "", cur.get("id") or 0):
            best[key] = e
    return list(best.values())


# ---------------------------------------------------------------------------
# Events — write
# ---------------------------------------------------------------------------

def _insert_observation(conn: sqlite3.Connection, event_id: int, obs: dict, observed_at: str) -> None:
    conn.execute(
        """INSERT INTO event_observations
           (event_id, source, observation_type, url, source_confidence,
            extraction_confidence, confidence, observed_at, checksum,
            time_start, time_end, stage)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event_id,
            obs.get("source"),
            obs.get("observation_type"),
            obs.get("url"),
            obs.get("source_confidence"),
            obs.get("extraction_confidence"),
            obs.get("confidence"),
            obs.get("observed_at") or observed_at,
            obs.get("checksum"),
            obs.get("time_start"),
            obs.get("time_end"),
            obs.get("stage"),
        ),
    )


def _upsert_observation(conn: sqlite3.Connection, event_id: int, obs: dict, observed_at: str) -> None:
    """
    Same source asserting the same content -> refresh observed_at (seen again).
    Same source asserting DIFFERENT content -> a new observation (a real re-sighting).
    """
    row = conn.execute(
        "SELECT id FROM event_observations WHERE event_id = ? AND source IS ? AND checksum IS ?",
        (event_id, obs.get("source"), obs.get("checksum")),
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE event_observations SET observed_at = ? WHERE id = ?",
            (obs.get("observed_at") or observed_at, row["id"]),
        )
    else:
        _insert_observation(conn, event_id, obs, observed_at)


def upsert_events(events: list[dict], run_id: str, path: Path = DB_PATH) -> dict:
    """
    Upsert canonical events BY IDENTITY (performer + venue + date), accumulating
    observations across runs.

    If the identity is new, insert the event and its observations. If it already
    exists, attach this run's observations to the EXISTING event and re-aggregate
    (confidence, source_count, verification_count, conflict) over ALL of its
    observations — so a second source corroborates rather than creating a
    duplicate event and discarding earlier provenance.

    Returns {"new": [...], "changed": [{before, after}], "unchanged": [...], "saved": n}.
    """
    from app.normalize.provenance import aggregate_observations, event_identity
    from app.reconcile.changes import _event_signature

    if not events:
        return {"new": [], "changed": [], "unchanged": [], "saved": 0}

    conn = get_connection(path)
    observed_at = datetime.now().isoformat()
    new: list[dict] = []
    changed: list[dict] = []
    unchanged: list[dict] = []

    for ev in events:
        try:
            key = event_identity(ev)
            obs_list = ev.get("observations") or []
            row = conn.execute("SELECT * FROM events WHERE identity_key = ?", (key,)).fetchone()

            if row is None:
                cur = conn.execute(
                    """INSERT INTO events
                       (identity_key, name, date, time_start, time_end, venue, performer, url,
                        stage, source, run_id, confidence, confidence_reason,
                        source_count, verification_count, conflict_flag, conflict_reason)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        key, ev.get("name"), ev.get("date"),
                        ev.get("time_start") or ev.get("time"), ev.get("time_end"),
                        ev.get("venue"), ev.get("performer"), ev.get("url"), ev.get("stage"),
                        ev.get("source", "unknown"), run_id,
                        ev.get("confidence"), ev.get("confidence_reason"),
                        ev.get("source_count"), ev.get("verification_count"),
                        ev.get("conflict_flag"), ev.get("conflict_reason"),
                    ),
                )
                event_id = cur.lastrowid
                for obs in obs_list:
                    _insert_observation(conn, event_id, obs, observed_at)
                new.append(ev)
                continue

            # Existing identity: accumulate this run's observations, then re-aggregate.
            event_id = row["id"]
            before = dict(row)
            for obs in obs_list:
                _upsert_observation(conn, event_id, obs, observed_at)

            all_obs = [dict(r) for r in conn.execute(
                "SELECT * FROM event_observations WHERE event_id = ?", (event_id,)
            ).fetchall()]
            agg = aggregate_observations(all_obs)
            primary = agg["primary"]
            resolved = agg["resolved_fields"]

            conn.execute(
                """UPDATE events SET time_start = ?, time_end = ?, stage = ?, url = ?, source = ?,
                                     confidence = ?, confidence_reason = ?, source_count = ?,
                                     verification_count = ?, conflict_flag = ?, conflict_reason = ?,
                                     run_id = ?
                    WHERE id = ?""",
                (
                    resolved.get("time_start"), resolved.get("time_end"), resolved.get("stage"),
                    resolved.get("url") or before.get("url"), primary.get("source"),
                    agg["confidence"], agg["confidence_reason"], agg["source_count"],
                    agg["verification_count"], agg["conflict_flag"], agg["conflict_reason"],
                    run_id, event_id,
                ),
            )
            after = dict(conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone())
            if _event_signature(before) != _event_signature(after):
                changed.append({"before": before, "after": after})
            else:
                unchanged.append(after)
        except Exception as exc:
            logger.warning("Failed to upsert event %s: %s", ev.get("name"), exc)

    conn.commit()
    conn.close()
    return {
        "new": new,
        "changed": changed,
        "unchanged": unchanged,
        "saved": len(new) + len(changed) + len(unchanged),
    }


def save_events(events: list[dict], run_id: str, path: Path = DB_PATH) -> int:
    """Backwards-compatible wrapper around upsert_events; returns the count written."""
    return upsert_events(events, run_id=run_id, path=path)["saved"]


def purge_past_events(before: Optional[str] = None, path: Path = DB_PATH) -> int:
    """
    Permanently delete events (and their observations) dated before `before`
    (defaults to today, local date, YYYY-MM-DD). This is a different kind of
    removal than the pipeline's "never infer removal" policy (see
    app/monitor.py) — that policy exists because a crawl not re-observing an
    event doesn't mean the event went away. A date in the past is not an
    inference; it's an unambiguous fact. Safe to re-run. Returns the number
    of events deleted.
    """
    cutoff = before or datetime.now().strftime("%Y-%m-%d")
    conn = get_connection(path)
    ids = [row["id"] for row in conn.execute(
        "SELECT id FROM events WHERE date < ?", (cutoff,)
    ).fetchall()]
    if ids:
        placeholders = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM event_observations WHERE event_id IN ({placeholders})", ids)
        conn.execute(f"DELETE FROM events WHERE id IN ({placeholders})", ids)
        conn.commit()
    conn.close()
    logger.info("Purged %d events dated before %s", len(ids), cutoff)
    return len(ids)


def purge_dateless_events(path: Path = DB_PATH) -> int:
    """
    Delete events with no resolvable ISO date (NULL, empty, or anything not
    matching YYYY-MM-DD). build_observation() now refuses to save one at
    ingest time (confirmed live 2026-08-09: a "This Saturday"-dated flyer
    with no absolute date anywhere landed with date=NULL and broke the
    dashboard's date-grouping JS -- rendered a literal "undefined, undefined
    NaN" section header), but purge_past_events()'s `date < cutoff` never
    matches a NULL/non-comparable date, so a row saved before that guard
    existed survives every run. Safe to re-run. Returns the number deleted.
    """
    conn = get_connection(path)
    ids = [row["id"] for row in conn.execute(
        "SELECT id FROM events WHERE date IS NULL OR date NOT GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'"
    ).fetchall()]
    if ids:
        placeholders = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM event_observations WHERE event_id IN ({placeholders})", ids)
        conn.execute(f"DELETE FROM events WHERE id IN ({placeholders})", ids)
        conn.commit()
    conn.close()
    logger.info("Purged %d dateless events", len(ids))
    return len(ids)


def purge_recurring_series_placeholder_events(path: Path = DB_PATH) -> int:
    """
    One-time cleanup for events saved before a bare series/program title was
    added to app.crawlers.sowal.RECURRING_SERIES_TITLES -- see that
    frozenset's docstring: a crawl before a per-date lineup is announced (or
    with no prose-lineup match) invents a fake performer out of the series
    name itself (e.g. "Watersound First Friday Concert Series" saved as if
    it were a real act, confirmed live 2026-09-03, four dates Sep-Dec, all
    venue=None). Adding a title to that set only stops NEW crawls from
    repeating the mistake -- same relationship as purge_non_music_events()
    to detect_non_music() -- so a title added today doesn't retroactively
    fix rows a past crawl already saved before the title was added; this
    does that other half.

    An exact performer-name match against a bare series title can never be
    a real act's own name, so this never risks deleting anything else.
    Safe to re-run; returns the number of events deleted.
    """
    from app.crawlers.sowal import RECURRING_SERIES_TITLES

    conn = get_connection(path)
    placeholders = ",".join("?" * len(RECURRING_SERIES_TITLES))
    ids = [row["id"] for row in conn.execute(
        f"SELECT id FROM events WHERE performer IN ({placeholders})",
        list(RECURRING_SERIES_TITLES),
    ).fetchall()]

    if ids:
        id_placeholders = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM event_observations WHERE event_id IN ({id_placeholders})", ids)
        conn.execute(f"DELETE FROM events WHERE id IN ({id_placeholders})", ids)
        conn.commit()
    conn.close()
    logger.info("Purged %d recurring-series placeholder event(s)", len(ids))
    return len(ids)


def purge_non_music_events(path: Path = DB_PATH) -> int:
    """
    One-time cleanup for events saved before app.crawlers.sowal learned to
    exclude non-music community-calendar listings (farmers markets, guided
    park tours, car shows, ...) -- see detect_non_music() in sowal.py. That
    fix only stops NEW ones from being saved; this retroactively removes
    rows already in the database whose performer (the whole event title,
    since these have no named act) matches the same detector.

    Safe to re-run; returns the number of events deleted.
    """
    from app.crawlers.sowal import detect_non_music

    conn = get_connection(path)
    rows = conn.execute("SELECT id, performer FROM events").fetchall()
    ids = [row["id"] for row in rows if detect_non_music(row["performer"])]

    if ids:
        placeholders = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM event_observations WHERE event_id IN ({placeholders})", ids)
        conn.execute(f"DELETE FROM events WHERE id IN ({placeholders})", ids)
        conn.commit()
    conn.close()
    logger.info("Purged %d non-music events", len(ids))
    return len(ids)


def resolve_sowal_conflicts(path: Path = DB_PATH) -> dict:
    """
    Policy: when a direct venue-site crawler (any source other than "sowal")
    and the sowal aggregator report DIFFERENT performers at the exact same
    (venue, date, time_start), that's almost always one real slot described
    two ways -- not two real bookings (e.g. SoWal's "Wrestle with Jimmy" vs.
    AJ's own site's "Jarred McConnell & High Aces", both Fri/Sat 9pm at AJ's
    Grayton Beach). The venue's own site wins: the sowal-only event is
    dropped, the site-sourced event is kept.

    Only triggers on an exact (venue, date, time_start) match with DIFFERENT
    performers -- same-performer corroboration across sources already merges
    into one event via identity_key and never reaches this function.

    Safe to re-run. Returns {"conflicts_found", "events_deleted"}.
    """
    conn = get_connection(path)
    groups = conn.execute("""
        SELECT LOWER(venue) AS v, date, time_start, GROUP_CONCAT(id) AS ids
        FROM events
        WHERE venue IS NOT NULL AND date IS NOT NULL AND time_start IS NOT NULL
        GROUP BY v, date, time_start
        HAVING COUNT(*) > 1
    """).fetchall()

    conflicts = 0
    deleted_ids: list[int] = []
    for group in groups:
        ids = [int(i) for i in group["ids"].split(",")]
        placeholders = ",".join("?" * len(ids))
        sources_by_event: dict[int, set] = {}
        for row in conn.execute(
            f"SELECT DISTINCT event_id, source FROM event_observations WHERE event_id IN ({placeholders})",
            ids,
        ).fetchall():
            sources_by_event.setdefault(row["event_id"], set()).add(row["source"])

        sowal_only = [eid for eid, srcs in sources_by_event.items() if srcs == {"sowal"}]
        has_site_source = any(srcs - {"sowal"} for srcs in sources_by_event.values())
        if sowal_only and has_site_source:
            conflicts += 1
            deleted_ids.extend(sowal_only)

    if deleted_ids:
        placeholders = ",".join("?" * len(deleted_ids))
        conn.execute(f"DELETE FROM event_observations WHERE event_id IN ({placeholders})", deleted_ids)
        conn.execute(f"DELETE FROM events WHERE id IN ({placeholders})", deleted_ids)
        conn.commit()

    conn.close()
    logger.info(
        "Resolved %d sowal/site time-slot conflicts, deleted %d sowal-only events",
        conflicts, len(deleted_ids),
    )
    return {"conflicts_found": conflicts, "events_deleted": len(deleted_ids)}


def resolve_stale_url_relistings(path: Path = DB_PATH) -> dict:
    """
    Policy: when the SAME source crawl re-describes the exact same
    real-world listing (identical source + url + date) with a different
    performer string across two separate daily runs -- a placeholder got
    filled in, a redundant "at Venue" suffix got trimmed, or the site
    itself corrected/changed the act -- that's one slot re-described, not
    a double-booking. A differing performer produces a different
    identity_key though, so the earlier wording never gets cleaned up on
    its own and just sits next to the corrected row looking like two acts
    booked at once (confirmed live 2026-08-07: Shunk Gulley's Tockify feed
    reported "Chris Johnson" for the same slot/detail-link on Aug 6's crawl
    and "David Dunavent" on Aug 7's; same pattern hit favorites_watch and
    sowal the same day -- "Watersound Town Center" -> "Grayson Capps and
    Kristy Lee Trio", "Bubbles & Beauty at The Pearl Hotel" -> "Bubbles &
    Beauty"). Resolution: within each (source, url, date) group, keep only
    the event with the most recent observation and drop the rest.

    Deliberately keyed on (source, url, date), not url alone -- aggregators
    like sowal reuse one series/category page URL across dozens of
    unrelated dates and performers (e.g. a recurring residency's listing),
    so grouping on url alone would wrongly collapse real distinct bookings
    that merely share a page. Restricting to same date keeps only the
    single-occurrence links this rule is meant for.

    Safe to re-run. Returns {"groups_found", "events_deleted"}.
    """
    conn = get_connection(path)
    rows = conn.execute("""
        SELECT e.id AS id, e.source AS source, e.url AS url, e.date AS date,
               MAX(eo.observed_at) AS latest_observed
        FROM events e
        JOIN event_observations eo ON eo.event_id = e.id
        WHERE e.url IS NOT NULL AND e.url != '' AND e.source IS NOT NULL
        GROUP BY e.id
    """).fetchall()

    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        key = (row["source"], row["url"], row["date"])
        groups.setdefault(key, []).append(dict(row))

    deleted_ids: list[int] = []
    groups_found = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        groups_found += 1
        keep = max(members, key=lambda r: r["latest_observed"])
        deleted_ids.extend(r["id"] for r in members if r["id"] != keep["id"])

    if deleted_ids:
        placeholders = ",".join("?" * len(deleted_ids))
        conn.execute(f"DELETE FROM event_observations WHERE event_id IN ({placeholders})", deleted_ids)
        conn.execute(f"DELETE FROM events WHERE id IN ({placeholders})", deleted_ids)
        conn.commit()

    conn.close()
    logger.info(
        "Resolved %d stale-relisting groups, deleted %d superseded events",
        groups_found, len(deleted_ids),
    )
    return {"groups_found": groups_found, "events_deleted": len(deleted_ids)}


def resolve_stale_image_relistings(path: Path = DB_PATH) -> dict:
    """
    Rule of thumb (set 2026-08-07): when a venue's own screenshot/flyer gets
    re-captured and it disagrees with an earlier capture of the same slot,
    trust the more recent screenshot -- venues edit their own Instagram
    lineup graphics after posting (confirmed live: North Beach Social's
    August flyer read "12 Eleven" + "Tbd" for Aug 8/22 in an earlier capture
    and "Cadillac Willy" + "The Typos" for the same two slots in a later
    capture of the SAME post, provable because the later capture's like
    count was strictly higher -- likes only accumulate, so it's unambiguous
    which capture came second).

    Deliberately scoped to image-vs-image collisions only -- within each
    (venue, date, time_start, stage) group, if 2+ events came from an
    "image:..." source with different performers, keep the one with the
    latest observation and drop the rest. Does NOT touch collisions
    involving a non-image source (sowal, a direct venue-site crawler):
    unlike two captures of one venue-controlled graphic, an aggregator and
    a screenshot updating at different paces isn't "the same post edited,"
    so which one is stale needs a human call (see resolve_sowal_conflicts
    for the analogous site-vs-sowal policy, and detect_schedule_conflicts's
    same_night_collision for surfacing this kind of case for review).

    Stage is part of the grouping key for the same reason
    detect_schedule_conflicts uses it: a multi-stage venue can legitimately
    host two acts at the same time on different stages, which must never
    collapse into one.

    Safe to re-run. Returns {"groups_found", "events_deleted"}.
    """
    conn = get_connection(path)
    rows = conn.execute("""
        SELECT e.id AS id, e.venue AS venue, e.date AS date, e.time_start AS time_start,
               e.stage AS stage, MAX(eo.observed_at) AS latest_observed
        FROM events e
        JOIN event_observations eo ON eo.event_id = e.id
        WHERE e.source LIKE 'image:%'
          AND e.venue IS NOT NULL AND e.date IS NOT NULL AND e.time_start IS NOT NULL
        GROUP BY e.id
    """).fetchall()

    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        key = ((row["venue"] or "").strip().lower(), row["date"], row["time_start"],
               (row["stage"] or "").strip().lower())
        groups.setdefault(key, []).append(dict(row))

    deleted_ids: list[int] = []
    groups_found = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        groups_found += 1
        keep = max(members, key=lambda r: r["latest_observed"])
        deleted_ids.extend(r["id"] for r in members if r["id"] != keep["id"])

    if deleted_ids:
        placeholders = ",".join("?" * len(deleted_ids))
        conn.execute(f"DELETE FROM event_observations WHERE event_id IN ({placeholders})", deleted_ids)
        conn.execute(f"DELETE FROM events WHERE id IN ({placeholders})", deleted_ids)
        conn.commit()

    conn.close()
    logger.info(
        "Resolved %d stale-image-relisting groups, deleted %d superseded events",
        groups_found, len(deleted_ids),
    )
    return {"groups_found": groups_found, "events_deleted": len(deleted_ids)}


def collapse_same_slot_duplicates_in_db(path: Path = DB_PATH) -> dict:
    """
    Retroactive counterpart to app.normalize.provenance.collapse_same_slot_
    duplicates(): that function only collapses variants seen together within
    ONE run's raw batch, so a duplicate that already exists as separate rows
    in the events table from an earlier run -- before this function existed,
    or because a later run's crawl only re-observed one of the venue-text
    variants at a time -- never gets merged, and just sits on the dashboard
    forever (confirmed live: "The Typos" stayed listed at "Red Fish Taco",
    "Papa Surf", and "Papa Surf (Nate & Matt)" for 2026-09-10 two days after
    the in-batch fix shipped, because all three rows were already stored
    from a run on 2026-09-01).

    Groups by (performer, date, time_start) -- ignoring venue -- same as the
    in-batch version. Winner precedence: highest-confidence flyer/screenshot
    reading (observation_type "image"/"ocr") wins; a tie, or no event in the
    group having one, falls through to whichever event's newest observation
    was observed most recently. That's deliberately NOT the in-batch
    version's "first-seen" fallback -- there's no real notion of "recency"
    between events processed in the same run's batch, but here each event's
    observations carry a real observed_at that can be days apart, so "most
    recent wins" is the meaningful choice, and it's the same policy this
    pipeline already uses for the identical class of conflict elsewhere
    (resolve_stale_url_relistings/resolve_stale_image_relistings). Confirmed
    this matters, not just tidier: on the real "The Typos" 2026-09-10 data,
    two variants tied at flyer confidence 0.80 ("Red Fish Taco", observed
    14:20, vs. "Papa Surf", observed 17:02) -- first-seen would have kept
    the wrong, earlier one; most-recent picks "Papa Surf", matching what
    commit 9c23041's manual investigation of this exact booking determined.

    Every losing event's observations are reassigned onto the winner rather
    than discarded, so provenance survives even though the losing venue
    text doesn't; the winner's aggregate fields (confidence, source_count,
    ...) are recomputed over the merged observation set, same as
    upsert_events() does for two observations of the same event landing in
    one run.

    Safe to re-run. Returns {"groups_found", "events_merged"}.
    """
    from app.normalize.provenance import aggregate_observations

    conn = get_connection(path)
    groups_rows = conn.execute("""
        SELECT LOWER(performer) AS p, date, time_start, GROUP_CONCAT(id) AS ids
        FROM events
        WHERE performer IS NOT NULL AND date IS NOT NULL AND time_start IS NOT NULL
        GROUP BY p, date, time_start
        HAVING COUNT(*) > 1
    """).fetchall()

    groups_found = 0
    events_merged = 0
    for group in groups_rows:
        ids = [int(i) for i in group["ids"].split(",")]
        placeholders = ",".join("?" * len(ids))
        obs_by_event: dict[int, list[dict]] = {i: [] for i in ids}
        for row in conn.execute(
            f"SELECT * FROM event_observations WHERE event_id IN ({placeholders})", ids
        ).fetchall():
            obs_by_event[row["event_id"]].append(dict(row))

        def flyer_confidence(event_id: int) -> float:
            confidences = [
                o.get("confidence") or 0.0
                for o in obs_by_event[event_id]
                if o.get("observation_type") in ("image", "ocr")
            ]
            return max(confidences) if confidences else -1.0

        def latest_observed(event_id: int) -> str:
            observed = [o.get("observed_at") or "" for o in obs_by_event[event_id]]
            return max(observed) if observed else ""

        winner_id = max(ids, key=lambda i: (flyer_confidence(i), latest_observed(i)))
        loser_ids = [i for i in ids if i != winner_id]
        if not loser_ids:
            continue

        groups_found += 1
        events_merged += len(loser_ids)

        loser_placeholders = ",".join("?" * len(loser_ids))
        conn.execute(
            f"UPDATE event_observations SET event_id = ? WHERE event_id IN ({loser_placeholders})",
            [winner_id, *loser_ids],
        )
        conn.execute(f"DELETE FROM events WHERE id IN ({loser_placeholders})", loser_ids)

        merged_obs = [dict(r) for r in conn.execute(
            "SELECT * FROM event_observations WHERE event_id = ?", (winner_id,)
        ).fetchall()]
        agg = aggregate_observations(merged_obs)
        resolved = agg["resolved_fields"]
        conn.execute(
            """UPDATE events SET time_start = ?, time_end = ?, stage = ?, url = ?, source = ?,
                                 confidence = ?, confidence_reason = ?, source_count = ?,
                                 verification_count = ?, conflict_flag = ?, conflict_reason = ?
                WHERE id = ?""",
            (
                resolved.get("time_start"), resolved.get("time_end"), resolved.get("stage"),
                resolved.get("url"), agg["primary"].get("source"),
                agg["confidence"], agg["confidence_reason"], agg["source_count"],
                agg["verification_count"], agg["conflict_flag"], agg["conflict_reason"],
                winner_id,
            ),
        )

    conn.commit()
    conn.close()
    logger.info(
        "Collapsed %d same-slot duplicate group(s), merged %d event(s)",
        groups_found, events_merged,
    )
    return {"groups_found": groups_found, "events_merged": events_merged}


def detect_schedule_conflicts(path: Path = DB_PATH) -> list[dict]:
    """
    Read-only diagnostic scan for the bug class that produced duplicate,
    conflicting Red Fish Taco and Shelby's Beach Bar rows for August 2026
    (confirmed 2026-08-02): a dense monthly-calendar flyer got its day
    numbers misread by GPT-4o Vision -- sometimes inconsistently across two
    separate runs of the SAME image -- landing the same real booking on the
    wrong date, a day or two off from where it actually belongs.

    Never modifies anything. Fixing a misread needs the actual flyer read by
    hand (see recanonicalize / manual corrections in recent commits); this
    only surfaces candidates so that doesn't require eyeballing every
    venue's month by hand. Meant to run every pipeline execution and get
    logged, not to be invoked ad hoc.

    Two finding types:
      - "same_night_collision": within one (venue, date), 2+ distinct
        performers share the same (stage, time_start) pairing -- confirmed
        live: a multi-stage venue (AJ's Grayton's Main Stage/Courtyard
        Stage/Round Room) can legitimately host several acts at the exact
        same time, so two acts only collide if they're also on the same
        stage (or stage is unknown for both). Venues with genuinely
        separate slots and no stage info (brunch + dinner, a festival's
        parallel events) always give each act its own distinct time, so
        requiring a stage+time match/ambiguity is what tells a real
        double-booking (or two differently-titled dupes of the same act)
        apart from an ordinary multi-slot day. This also catches
        un-canonicalized name variants of the same act billed at the same
        time (e.g. "X" vs "Songwriter X").
      - "irregular_recurrence": one (venue, performer) pair has both a
        roughly-weekly gap (6-8 days) AND a short gap (1-3 days) somewhere
        in its booking dates -- the signature of a single real weekly slot
        that landed on two nearby-but-wrong dates. A true nightly/frequent
        residency (gaps consistently 1-4 days) and a clean weekly residency
        (gaps consistently ~7 days) never produce this specific mix, so
        neither is flagged -- confirmed against this app's actual data
        (Eric Knight nightly at The Village Door, Dion Jones weekly at
        Stinky's Bait Shack) before landing on this rule.
    """
    conn = get_connection(path)
    events = conn.execute(
        "SELECT id, performer, venue, date, time_start, stage FROM events "
        "WHERE date IS NOT NULL AND venue IS NOT NULL AND performer IS NOT NULL"
    ).fetchall()
    conn.close()

    findings: list[dict] = []

    by_venue_date: dict[tuple, list] = {}
    for e in events:
        by_venue_date.setdefault(((e["venue"] or "").strip().lower(), e["date"]), []).append(e)
    for (_v, date), rows in by_venue_date.items():
        if len({r["performer"] for r in rows if r["performer"]}) < 2:
            continue
        by_slot: dict[tuple, set] = {}
        for r in rows:
            slot = ((r["stage"] or "").strip().lower() or None, r["time_start"] or None)
            by_slot.setdefault(slot, set()).add(r["performer"])
        colliding = {p for performers in by_slot.values() if len(performers) > 1 for p in performers}
        if colliding:
            findings.append({
                "type": "same_night_collision",
                "venue": rows[0]["venue"],
                "date": date,
                "performers": sorted(colliding),
            })

    by_venue_performer: dict[tuple, list] = {}
    for e in events:
        key = ((e["venue"] or "").strip().lower(), (e["performer"] or "").strip().lower())
        by_venue_performer.setdefault(key, []).append(e)
    for (_v, _p), rows in by_venue_performer.items():
        if len(rows) < 2:
            continue
        dated = []
        for r in rows:
            try:
                y, m, d = (int(x) for x in r["date"].split("-"))
                dated.append((_pydate(y, m, d), r))
            except (ValueError, AttributeError):
                continue
        dated.sort(key=lambda t: t[0])
        gaps = [(dated[i + 1][0] - dated[i][0]).days for i in range(len(dated) - 1)]
        has_weekly_gap = any(6 <= g <= 8 for g in gaps)
        has_short_gap = any(1 <= g <= 3 for g in gaps)
        if has_weekly_gap and has_short_gap:
            findings.append({
                "type": "irregular_recurrence",
                "venue": dated[0][1]["venue"],
                "performer": dated[0][1]["performer"],
                "dates": [dt.isoformat() for dt, _ in dated],
            })

    return findings


def purge_source_observations(source: str, path: Path = DB_PATH) -> dict:
    """
    Delete every observation from `source`, then drop any event left with
    zero observations (nothing else corroborates it) and recompute
    aggregates for events that still have at least one — e.g. an event that
    was source_count=2 (this source + another) drops back to source_count=1
    once this source's observation is gone, instead of staying stale.

    For a source whose data has gone stale and duplicates a still-active
    source under slightly different performer names, this is the deliberate
    fix: safe to re-run, and distinct from the "never infer removal from a
    crawl gap" policy in app/monitor.py, since here removal isn't inferred
    from absence — the whole source is being retired on purpose.

    Returns {"observations_deleted", "events_deleted", "events_recomputed"}.
    """
    conn = get_connection(path)
    obs_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM event_observations WHERE source = ?", (source,)
    ).fetchall()]
    affected_event_ids = [r["event_id"] for r in conn.execute(
        "SELECT DISTINCT event_id FROM event_observations WHERE source = ?", (source,)
    ).fetchall()]

    if obs_ids:
        placeholders = ",".join("?" * len(obs_ids))
        conn.execute(f"DELETE FROM event_observations WHERE id IN ({placeholders})", obs_ids)
        conn.commit()

    events_deleted = 0
    if affected_event_ids:
        placeholders = ",".join("?" * len(affected_event_ids))
        orphaned = [r["id"] for r in conn.execute(
            f"SELECT id FROM events WHERE id IN ({placeholders}) "
            "AND id NOT IN (SELECT DISTINCT event_id FROM event_observations)",
            affected_event_ids,
        ).fetchall()]
        if orphaned:
            op = ",".join("?" * len(orphaned))
            conn.execute(f"DELETE FROM events WHERE id IN ({op})", orphaned)
            conn.commit()
            events_deleted = len(orphaned)

    conn.close()
    recomputed = recompute_aggregates(path) if obs_ids else 0
    logger.info(
        "Purged %d observations from source=%s: deleted %d orphaned events, recomputed %d",
        len(obs_ids), source, events_deleted, recomputed,
    )
    return {
        "observations_deleted": len(obs_ids),
        "events_deleted": events_deleted,
        "events_recomputed": recomputed,
    }


def recompute_aggregates(path: Path = DB_PATH) -> int:
    """
    Re-derive every event's aggregate (confidence, source/verification counts,
    conflict) from its stored observations. Used after the aggregation rules
    change. Returns the number of events updated.
    """
    from app.normalize.provenance import aggregate_observations

    conn = get_connection(path)
    updated = 0
    for row in conn.execute("SELECT id FROM events").fetchall():
        obs = [dict(r) for r in conn.execute(
            "SELECT * FROM event_observations WHERE event_id = ?", (row["id"],)
        ).fetchall()]
        if not obs:
            continue
        agg = aggregate_observations(obs)
        resolved = agg["resolved_fields"]
        conn.execute(
            """UPDATE events SET time_start = ?, time_end = ?, stage = ?,
                                 confidence = ?, confidence_reason = ?, source_count = ?,
                                 verification_count = ?, conflict_flag = ?, conflict_reason = ?
                WHERE id = ?""",
            (resolved.get("time_start"), resolved.get("time_end"), resolved.get("stage"),
             agg["confidence"], agg["confidence_reason"], agg["source_count"],
             agg["verification_count"], agg["conflict_flag"], agg["conflict_reason"], row["id"]),
        )
        updated += 1
    conn.commit()
    conn.close()
    logger.info("Recomputed aggregates for %d events", updated)
    return updated


def recanonicalize_venues(path: Path = DB_PATH) -> dict:
    """
    Re-apply venue canonicalization (app.normalize.canonical.canonicalize) to
    every stored event. New aliases added to CANONICAL_FIXES only affect
    events ingested AFTER the alias is added — this retroactively rewrites
    venue + identity_key on existing rows and, when the rename makes two
    already-saved events collide on identity, collapses them into one
    canonical event (re-pointing observations, deleting the redundant row) —
    the same collapse pattern _migration_6 uses for cross-run accumulation,
    applied here for renames instead.

    Safe to re-run any time new venue aliases are added to canonical.py.
    Returns {"renamed": N, "merged": M}.
    """
    from app.normalize.canonical import canonicalize
    from app.normalize.provenance import event_identity

    conn = get_connection(path)
    rows = [dict(r) for r in conn.execute(
        "SELECT id, performer, venue, date, identity_key FROM events"
    ).fetchall()]

    # Compute the post-canonicalization identity for every row BEFORE writing
    # anything, so collisions caused by the rename are detected up front
    # rather than tripping the UNIQUE identity_key index mid-loop.
    by_new_identity: dict[str, list[dict]] = {}
    for row in rows:
        new_venue = canonicalize(row["venue"])
        new_identity = event_identity({
            "performer": row["performer"], "venue": new_venue, "date": row["date"],
        })
        by_new_identity.setdefault(new_identity, []).append({**row, "new_venue": new_venue})

    renamed = 0
    merged = 0
    for new_identity, group in by_new_identity.items():
        keep = min(group, key=lambda r: r["id"])
        others = [r for r in group if r["id"] != keep["id"]]

        if others:
            other_ids = [r["id"] for r in others]
            placeholders = ",".join("?" * len(other_ids))
            conn.execute(
                f"UPDATE event_observations SET event_id = ? WHERE event_id IN ({placeholders})",
                (keep["id"], *other_ids),
            )
            conn.execute(f"DELETE FROM events WHERE id IN ({placeholders})", other_ids)
            merged += len(others)

        if keep["new_venue"] != keep["venue"] or others:
            new_name = f"{keep['performer']} at {keep['new_venue']}" if keep["performer"] else None
            if new_name:
                conn.execute(
                    "UPDATE events SET venue = ?, identity_key = ?, name = ? WHERE id = ?",
                    (keep["new_venue"], new_identity, new_name, keep["id"]),
                )
            else:
                conn.execute(
                    "UPDATE events SET venue = ?, identity_key = ? WHERE id = ?",
                    (keep["new_venue"], new_identity, keep["id"]),
                )
        if keep["new_venue"] != keep["venue"]:
            renamed += 1

    if merged:
        # Drop repeat observations of identical content from the same source
        # that may now collide under one event_id after the merge (same
        # cleanup _migration_6 does after its own duplicate collapse).
        conn.execute(
            "DELETE FROM event_observations WHERE id NOT IN "
            "(SELECT MIN(id) FROM event_observations GROUP BY event_id, source, checksum)"
        )

    conn.commit()
    conn.close()

    logger.info(
        "Recanonicalized %d event venues, merged %d duplicate rows into existing events",
        renamed, merged,
    )
    if merged:
        recompute_aggregates(path)

    return {"renamed": renamed, "merged": merged}


def backfill_venue_default_times(path: Path = DB_PATH) -> int:
    """
    Fill in time_start for events at a venue with a known standard slot
    (VENUE_DEFAULT_TIMES in app/normalize/times.py) that are still missing
    one. apply_venue_default_time() already runs at ingest time, but it's an
    exact string match on the CANONICAL venue name -- an event ingested
    under an un-canonicalized variant (e.g. "PapaSurf" before that alias
    existed) never matched, so it kept no time at all. recanonicalize_venues()
    fixes the venue name, and recompute_aggregates() backfills the time for
    any event that merged with a same-date counterpart that DID have one,
    but a standalone renamed event with no such counterpart stays blank
    forever unless something re-applies the default after the rename.
    Confirmed live 2026-08-08: five Papa Surf bookings extracted from a
    flyer that listed act + date with no time sat blank under "PapaSurf"
    until the alias was added, and even after the rename three of them
    (no same-date "Papa Surf" counterpart to inherit from) were still blank.

    Deliberately only touches events whose venue IS a VENUE_DEFAULT_TIMES
    key and whose time_start is currently missing -- never overwrites a
    real captured time. Safe to re-run; a no-op once times have converged.
    Returns the number of events backfilled.
    """
    from app.normalize.times import VENUE_DEFAULT_TIMES, apply_venue_default_time

    if not VENUE_DEFAULT_TIMES:
        return 0

    conn = get_connection(path)
    placeholders = ",".join("?" * len(VENUE_DEFAULT_TIMES))
    rows = conn.execute(
        f"SELECT id, venue, time_start FROM events WHERE venue IN ({placeholders})",
        list(VENUE_DEFAULT_TIMES),
    ).fetchall()

    backfilled = 0
    for row in rows:
        new_time = apply_venue_default_time(row["venue"], row["time_start"])
        if new_time != row["time_start"]:
            conn.execute("UPDATE events SET time_start = ? WHERE id = ?", (new_time, row["id"]))
            backfilled += 1

    if backfilled:
        conn.commit()
    conn.close()
    logger.info("Backfilled default time for %d venue-default-time events", backfilled)
    return backfilled


def normalize_stored_times(path: Path = DB_PATH) -> int:
    """
    Reformat every event's time_start/time_end to the canonical "H:MM AM/PM"
    (or "H:MM AM/PM - H:MM AM/PM") display format -- rule set added
    2026-08-08 to collapse the dozens of variants that had accumulated
    across sources ("6PM", "6-9 PM", "6:30 pm CT", "5:00 PM - 8:00 PM CST",
    ...); see times.py's split_time_range()/format_time_range() docstrings
    for the full parsing rules.

    split_time_range()/format_time_range() already run at ingest time (see
    provenance.py), so this is a one-time catch-up for rows saved before
    that existed, plus a standing safety net: backfill_venue_default_times()
    writes VENUE_DEFAULT_TIMES's raw string ("6:00 - 9:00 PM") straight into
    time_start without going through this formatting, so running this AFTER
    it (see resolve_and_finalize()) is what actually splits that default
    into a proper time_start/time_end pair too.

    Splits an in-band range crammed into time_start (e.g. "6-9 PM") into
    separate time_start/time_end fields, same as ingest-time behavior, so a
    stale pre-2026-08-08 row converges to the same shape a fresh one gets
    today -- this is also what makes the dashboard's time column show the
    full range instead of silently dropping time_end (render.py only ever
    displayed time_start; see render.py's _rows_html).

    Never touches a value split_time_range()/format_time_range() can't
    confidently parse -- an unparseable time is left exactly as stored
    rather than mangled. Safe to re-run; a no-op once times have converged.
    Returns the number of events changed.
    """
    from app.normalize.times import format_time_range, split_time_range

    conn = get_connection(path)
    rows = conn.execute("SELECT id, time_start, time_end FROM events").fetchall()

    changed = 0
    for row in rows:
        new_start, range_end = split_time_range(row["time_start"])
        new_end = range_end or (
            format_time_range(row["time_end"]) if row["time_end"] else row["time_end"]
        )
        if new_start != row["time_start"] or new_end != row["time_end"]:
            conn.execute(
                "UPDATE events SET time_start = ?, time_end = ? WHERE id = ?",
                (new_start, new_end, row["id"]),
            )
            changed += 1

    if changed:
        conn.commit()
    conn.close()
    logger.info("Normalized stored time format for %d events", changed)
    return changed


def recanonicalize_performers(path: Path = DB_PATH) -> dict:
    """
    Re-apply performer canonicalization (app.normalize.canonical.canonicalize)
    to every stored event. Same purpose and pattern as recanonicalize_venues()
    — new CANONICAL_FIXES aliases only affect events ingested AFTER the alias
    is added, so this retroactively fixes already-saved rows and collapses
    any resulting identity collisions.

    Safe to re-run any time new performer aliases are added to canonical.py.
    Returns {"renamed": N, "merged": M}.
    """
    from app.normalize.canonical import canonicalize
    from app.normalize.provenance import event_identity

    conn = get_connection(path)
    rows = [dict(r) for r in conn.execute(
        "SELECT id, performer, venue, date, identity_key FROM events"
    ).fetchall()]

    by_new_identity: dict[str, list[dict]] = {}
    for row in rows:
        new_performer = canonicalize(row["performer"])
        new_identity = event_identity({
            "performer": new_performer, "venue": row["venue"], "date": row["date"],
        })
        by_new_identity.setdefault(new_identity, []).append({**row, "new_performer": new_performer})

    renamed = 0
    merged = 0
    for new_identity, group in by_new_identity.items():
        keep = min(group, key=lambda r: r["id"])
        others = [r for r in group if r["id"] != keep["id"]]

        if others:
            other_ids = [r["id"] for r in others]
            placeholders = ",".join("?" * len(other_ids))
            conn.execute(
                f"UPDATE event_observations SET event_id = ? WHERE event_id IN ({placeholders})",
                (keep["id"], *other_ids),
            )
            conn.execute(f"DELETE FROM events WHERE id IN ({placeholders})", other_ids)
            merged += len(others)

        if keep["new_performer"] != keep["performer"] or others:
            new_name = f"{keep['new_performer']} at {keep['venue']}" if keep["venue"] else keep["new_performer"]
            conn.execute(
                "UPDATE events SET performer = ?, identity_key = ?, name = ? WHERE id = ?",
                (keep["new_performer"], new_identity, new_name, keep["id"]),
            )
        if keep["new_performer"] != keep["performer"]:
            renamed += 1

    if merged:
        conn.execute(
            "DELETE FROM event_observations WHERE id NOT IN "
            "(SELECT MIN(id) FROM event_observations GROUP BY event_id, source, checksum)"
        )

    conn.commit()
    conn.close()

    logger.info(
        "Recanonicalized %d event performers, merged %d duplicate rows into existing events",
        renamed, merged,
    )
    if merged:
        recompute_aggregates(path)

    return {"renamed": renamed, "merged": merged}


def load_event_observations(event_id: int, path: Path = DB_PATH) -> list[dict]:
    """Load the observations (event_observations rows) for a canonical event."""
    conn = get_connection(path)
    rows = conn.execute(
        "SELECT * FROM event_observations WHERE event_id = ? ORDER BY confidence DESC",
        (event_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
