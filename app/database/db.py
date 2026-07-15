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

            conn.execute(
                """UPDATE events SET time_start = ?, time_end = ?, stage = ?, url = ?, source = ?,
                                     confidence = ?, confidence_reason = ?, source_count = ?,
                                     verification_count = ?, conflict_flag = ?, conflict_reason = ?,
                                     run_id = ?
                    WHERE id = ?""",
                (
                    primary.get("time_start"), primary.get("time_end"), primary.get("stage"),
                    primary.get("url") or before.get("url"), primary.get("source"),
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
        primary = agg["primary"]
        conn.execute(
            """UPDATE events SET time_start = ?, time_end = ?, stage = ?,
                                 confidence = ?, confidence_reason = ?, source_count = ?,
                                 verification_count = ?, conflict_flag = ?, conflict_reason = ?
                WHERE id = ?""",
            (primary.get("time_start"), primary.get("time_end"), primary.get("stage"),
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
