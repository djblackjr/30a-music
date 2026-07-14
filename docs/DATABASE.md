# Database Reference

The 30A Music Intelligence database is a single SQLite file at **`data/events.db`**,
managed exclusively through `app/database/db.py`. This document describes the schema as
of **schema version 6**.

- Access layer: `app/database/db.py`
- Connection: `sqlite3` with `row_factory = sqlite3.Row`
- Current schema version: **6** (`PRAGMA user_version`)

> **Invariant:** all schema changes are **additive** and **auto-applied**. Opening an older
> database migrates it up in place (`ALTER ... ADD COLUMN`, `NULL`-guarded backfills) — no
> rebuild, no deletion, no data loss. This is verified for the v0 (pre-versioning) path.

---

## Tables

The database has two application tables plus SQLite's internal bookkeeping tables.

| Table            | Purpose                                                    |
|------------------|------------------------------------------------------------|
| `events`         | ONE canonical row per event identity (upserted across runs) |
| `event_observations` | Observations — one row per sighting of an event (a source can produce many) |
| `runs`           | One row per pipeline run (run tracking / reconciliation)   |
| `sqlite_sequence`| Internal — AUTOINCREMENT counters (managed by SQLite)      |

`events` holds **one canonical row per identity** (`performer + venue + date`), upserted.
A run does not insert a fresh copy of everything it saw — it **attaches its observations to
the existing event** and re-aggregates. That is what lets two sources seen in different runs
corroborate each other instead of becoming duplicate rows. The full history of *sightings*
lives in `event_observations`.

---

## Columns

### `events`

| Column       | Type    | Notes                                                        |
|--------------|---------|--------------------------------------------------------------|
| `id`         | INTEGER | Primary key, AUTOINCREMENT                                   |
| `identity_key` | TEXT  | `performer\|venue\|date` — UNIQUE; the upsert key (v6)       |
| `name`       | TEXT    | Display name, e.g. `"Stevie Monce at Chiringo"` (auto-filled)|
| `date`       | TEXT    | ISO 8601 `YYYY-MM-DD` (stored as text)                       |
| `time_start` | TEXT    | Free-form start time, e.g. `"6PM"`, `"8:30PM"`               |
| `time_end`   | TEXT    | Free-form end time; often NULL                              |
| `venue`      | TEXT    | Venue name                                                   |
| `performer`  | TEXT    | Artist / performer name                                      |
| `url`        | TEXT    | Source or artist link                                        |
| `stage`      | TEXT    | Stage within a venue, e.g. `"Main Stage"`; often NULL        |
| `source`     | TEXT    | Provenance, e.g. `seed`, `crawler`, `sowal`, `image:<file>`  |
| `run_id`     | TEXT    | The run that produced this row (FK-by-convention to `runs`); `'legacy'` for pre-run-tracking rows |
| `confidence` | REAL    | Aggregate event confidence `[0.0, 0.99]` (v2; aggregated across observations in v3) |
| `confidence_reason` | TEXT | Human-readable summary of how the score was derived (v2) |
| `source_count` | INTEGER | Number of distinct sources that observed this event (v3) |
| `verification_count` | INTEGER | Number of distinct sources agreeing with consensus (v3) |
| `conflict_flag` | INTEGER | 1 if observations disagree on a mutable field, else 0 (v3) |
| `conflict_reason` | TEXT | Description of the conflict, e.g. "Time mismatch: 6PM vs 7PM" (v3) |

All columns except `id` are nullable. Dates and times are stored as text; date comparison
relies on ISO 8601 sorting lexicographically (`date >= today`).

### `runs`

| Column         | Type    | Notes                                             |
|----------------|---------|---------------------------------------------------|
| `id`           | INTEGER | Primary key, AUTOINCREMENT                        |
| `run_id`       | TEXT    | UNIQUE — timestamp string `YYYYMMDD_HHMMSS`       |
| `started_at`   | TEXT    | ISO 8601 timestamp (`datetime.now().isoformat()`) |
| `events_saved` | INTEGER | Count of events persisted in that run (default 0) |

### `event_observations` (v3; renamed from `event_sources` in v4)

One row per **observation** — a single sighting of an event. One source (e.g. a venue
website) produces many observations over time, so the row is an observation, not a source.

| Column                  | Type    | Notes                                              |
|-------------------------|---------|----------------------------------------------------|
| `id`                    | INTEGER | Primary key, AUTOINCREMENT                         |
| `event_id`              | INTEGER | FK-by-convention to `events.id`                    |
| `source`                | TEXT    | who/where: e.g. `sowal`, `venue`, `image:<file>`, `seed` |
| `observation_type`      | TEXT    | how it was obtained: website / image / ocr / api / manual / social / calendar (v5) |
| `url`                   | TEXT    | Source link for this observation                   |
| `source_confidence`     | REAL    | Trust in the source itself                         |
| `extraction_confidence` | REAL    | How well this observation was read (completeness + model) |
| `confidence`            | REAL    | Effective per-observation score (source × extraction) |
| `observed_at`           | TEXT    | ISO timestamp when the observation was retrieved   |
| `checksum`              | TEXT    | Content hash of the observation (incremental-crawl hook) |
| `time_start`            | TEXT    | What THIS observation asserted — enables cross-run conflict detection (v6) |
| `time_end`              | TEXT    | What THIS observation asserted (v6)                |
| `stage`                 | TEXT    | What THIS observation asserted (v6)                |

---

## Primary keys

| Table    | Primary key            |
|----------|------------------------|
| `events` | `id` (INTEGER AUTOINCREMENT) |
| `runs`   | `id` (INTEGER AUTOINCREMENT) |

`events.id` and `runs.id` are surrogate keys. Business identity for events is **not** the
primary key — see [Event identity](#event-identity).

---

## Indexes

| Index                    | Table  | Columns    | Origin                          |
|--------------------------|--------|------------|---------------------------------|
| `sqlite_autoindex_runs_1`| `runs` | `run_id`   | Auto-created by the `UNIQUE` constraint |
| `idx_event_observations_event_id` | `event_observations` | `event_id` | Explicit — observations are looked up per event (v3/v4) |
| `idx_events_identity` | `events` | `identity_key` | **UNIQUE** — enforces one canonical event per identity (v6) |

`idx_events_identity` is what makes the upsert safe: it guarantees a single canonical event
per identity.

---

## Run history & reconciliation

Every invocation of the pipeline (`app/monitor.run_pipeline`) creates a run:

1. A `run_id` is generated from the current timestamp: `YYYYMMDD_HHMMSS`.
2. `upsert_events()` writes each canonical event **by identity**:
   - identity not seen before → insert the event + its observations (**new**)
   - identity already exists → attach this run's observations to the existing event and
     re-aggregate confidence / counts / conflict over **all** of them
     (**changed** if the content moved, otherwise **unchanged**)
3. `record_run(run_id, events_saved)` writes/updates the `runs` row.

**Observation upsert rule:** the same source asserting the same content (same `checksum`)
refreshes `observed_at` rather than inserting a duplicate. The same source asserting
*different* content records a genuine new observation.

**Removal is never inferred.** A run is a **partial view** — one crawl of one or two
sources — not a full snapshot of reality. A source failing to re-observe an event does not
mean the event was cancelled, so the pipeline reports `removed = 0` rather than flagging
every event the crawler simply didn't see. (`compare_runs()` in `app/reconcile/changes.py`
still exists for comparing two explicit event lists.)

---

## Event identity

The primary key (`id`) identifies a **row**, not an **event**. Business identity is defined
by the reconciliation engine (`app/reconcile/changes.py`) and confirmed during Phase 0:

> **Identity = `performer` + `venue` + `date`.**

- Two rows with the same performer, venue, and date represent the same event.
- **Mutable attributes** (not part of identity): `time_start`, `time_end`, `stage`, `url`,
  `source`, and (from v2) `confidence` / `confidence_reason`.
- **Change semantics:**
  - A time change at the same performer/venue/date → the event is **Changed**.
  - The same performer at two different venues on one day → two **distinct** events.
  - A `confidence` / `source` / `url` change alone → **not** classed as Changed
    (excluded from the change signature).

Identity is implemented by `_event_key(ev)` (the identity key) and change detection by
`_event_signature(ev)` (content hash). See `docs/MIGRATION.md` for the full model.

> **Note:** As of schema v1, `_event_key` does not yet include `venue`. Adding it is the
> reconciliation task scheduled for Phase 2; the identity model above is the agreed target.

---

## Confidence & provenance

**Two dimensions per observation, aggregated per event** (`app/normalize/confidence.py`,
`app/normalize/provenance.py`).

Each observation (row in `event_observations`) carries:
- `source_confidence` — trust in the source itself
- `extraction_confidence` — how well it was read (completeness + model-reported confidence)
- `confidence` — effective per-observation score = `source_confidence × extraction_confidence`

Each canonical event (row in `events`) aggregates its observations via the
`ConfidenceAggregator` into a single `confidence`:
1. start from the highest-confidence independent observation,
2. each additional **agreeing** independent source raises it toward 1 with diminishing returns,
3. a **conflict** applies a multiplicative penalty (and sets `conflict_flag`),
4. capped at **0.99** (never 1.00),
5. extra agreeing sources only add — a low-quality source never *reduces* a high-confidence
   event; only a direct conflict does.

The algorithm is encapsulated in `ConfidenceAggregator` so it can evolve without any
schema change. `source_count` / `verification_count` / `conflict_flag` / `conflict_reason`
record the provenance summary.

**Rendering rule:** the dashboard is "dumb" — it renders these precomputed values and never
recalculates confidence, reconciliation, venue defaults, or canonical names.

Bands used by the dashboard and Excel report: **high ≥ 0.80 · medium 0.50–0.79 · low < 0.50**.

`confidence`, provenance columns, `source`, and `url` are **excluded from the reconciliation
change signature**, so a score/provenance change alone does not mark an event as Changed.

---

## Migration strategy

The schema is versioned with SQLite's native **`PRAGMA user_version`** (an integer, `0` on a
fresh DB). All migration logic lives in `app/database/db.py`.

**Mechanism:**

- `SCHEMA_VERSION` — the latest version the code targets.
- `MIGRATIONS` — an ordered list of `(target_version, migration_fn)` tuples.
- `init_db(path)` reads the current `user_version`, runs each migration whose target is
  greater than the current version in order, and bumps `user_version` after each.

**Properties:**

- **Ordered** — migrations always run low-to-high.
- **Tracked** — the applied version is stored in the DB itself (`user_version`).
- **Idempotent** — `init_db()` is safe to call repeatedly; already-applied migrations are
  skipped, and individual migrations use `CREATE TABLE IF NOT EXISTS` /
  additive `ALTER ... ADD COLUMN` (via `_add_column_if_missing`), so a pre-versioning DB
  (detected as v0) upgrades in place with no data loss.

**Rules for adding a migration:**

1. Write a `_migration_N(conn)` function.
2. Append `(N, _migration_N)` to `MIGRATIONS`.
3. Bump `SCHEMA_VERSION` to `N`.
4. **Never edit a released migration in place** — always add a new one.

**Version history:**

| Version | Migration      | Change                                                            |
|---------|----------------|-------------------------------------------------------------------|
| 1       | `_migration_1` | Baseline: `events` + `runs` tables, plus `stage`/`source`/`run_id`|
| 2       | `_migration_2` | Add `confidence REAL` + `confidence_reason TEXT`; backfill confidence from source trust and `run_id='legacy'` for pre-run-tracking rows |
| 3       | `_migration_3` | Source provenance: add `source_count`/`verification_count`/`conflict_flag`/`conflict_reason` to `events`; create `event_sources` table + index |
| 4       | `_migration_4` | Rename `event_sources` → `event_observations` (in-place `ALTER TABLE`, data preserved); reindex |
| 5       | `_migration_5` | Add `event_observations.observation_type`; backfill existing rows from source |
| 6       | `_migration_6` | Cross-run accumulation: add `events.identity_key` (UNIQUE) + observation `time_start`/`time_end`/`stage`; collapse duplicate identities into one canonical event (re-pointing observations); recompute aggregates |

Helper: `get_schema_version(path)` returns the current `user_version` without running any
migration.
