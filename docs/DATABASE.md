# Database Reference

The 30A Music Intelligence database is a single SQLite file at **`data/events.db`**,
managed exclusively through `app/database/db.py`. This document describes the schema as
of **schema version 2**.

- Access layer: `app/database/db.py`
- Connection: `sqlite3` with `row_factory = sqlite3.Row`
- Current schema version: **2** (`PRAGMA user_version`)

> **Invariant:** all schema changes are **additive** and **auto-applied**. Opening an older
> database migrates it up in place (`ALTER ... ADD COLUMN`, `NULL`-guarded backfills) — no
> rebuild, no deletion, no data loss. This is verified for the v0 (pre-versioning) path.

---

## Tables

The database has two application tables plus SQLite's internal bookkeeping tables.

| Table            | Purpose                                                    |
|------------------|------------------------------------------------------------|
| `events`         | Every event captured, across all runs (append-only history)|
| `runs`           | One row per pipeline run (run tracking / reconciliation)   |
| `sqlite_sequence`| Internal — AUTOINCREMENT counters (managed by SQLite)      |

`events` is **append-only**: each pipeline run inserts a fresh copy of its events tagged
with that run's `run_id`. Old rows are never deleted, so the table is a full history.

---

## Columns

### `events`

| Column       | Type    | Notes                                                        |
|--------------|---------|--------------------------------------------------------------|
| `id`         | INTEGER | Primary key, AUTOINCREMENT                                   |
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
| `confidence` | REAL    | Extraction confidence `[0.0, 1.0]` (v2)                     |
| `confidence_reason` | TEXT | How the score was derived (v2)                         |

All columns except `id` are nullable. Dates and times are stored as text; date comparison
relies on ISO 8601 sorting lexicographically (`date >= today`).

### `runs`

| Column         | Type    | Notes                                             |
|----------------|---------|---------------------------------------------------|
| `id`           | INTEGER | Primary key, AUTOINCREMENT                        |
| `run_id`       | TEXT    | UNIQUE — timestamp string `YYYYMMDD_HHMMSS`       |
| `started_at`   | TEXT    | ISO 8601 timestamp (`datetime.now().isoformat()`) |
| `events_saved` | INTEGER | Count of events persisted in that run (default 0) |

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

There are currently **no user-defined indexes**. `events` is queried by `run_id` (in
`load_events`) and ordered by `date, time_start`; if the table grows large, a candidate
index is `(run_id, date, time_start)`. Not added yet — table size does not warrant it.

---

## Run history

Every invocation of the pipeline (`app/monitor.run_pipeline`) creates a run:

1. A `run_id` is generated from the current timestamp: `YYYYMMDD_HHMMSS`.
2. Events for that run are inserted into `events`, each tagged with the `run_id`.
3. `record_run(run_id, events_saved)` writes/updates the `runs` row
   (`INSERT OR REPLACE`, keyed on the UNIQUE `run_id`).

Because `events` is append-only, the database holds the complete history of every run.
Reconciliation compares the current run against the **previous** run:

- `get_last_run_id()` returns the second-most-recent run via
  `ORDER BY id DESC LIMIT 1 OFFSET 1`. The `OFFSET 1` skips the run just recorded, so it
  resolves to the prior run — the correct comparison baseline.
- `load_events(run_id=...)` loads a specific run's events; `load_events()` (no argument)
  loads the entire history.

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

## Confidence fields

**Status: columns present as of schema version 2.** The authoritative live scorer
(`app/normalize/confidence.py`) is delivered in Phase 2; until then, the v2 migration
backfills existing rows from source trust (see below), and `save_events` persists any
`confidence` / `confidence_reason` supplied on the event dict.

Every event carries a confidence score describing how much to trust the extraction.

| Column             | Type | Notes                                                       |
|--------------------|------|-------------------------------------------------------------|
| `confidence`       | REAL | Score in `[0.0, 1.0]`                                        |
| `confidence_reason`| TEXT | Human-readable explanation of how the score was derived     |

Scoring (computed in `app/normalize/confidence.py`, applied in the normalization pass):

- **Source trust (base weight):** structured crawler ≈ 0.9 · GPT-4o vision ≈ 0.8 ·
  Apple Vision OCR ≈ 0.5 · seed ≈ 0.6.
- **Field completeness:** valid ISO `date`, `time_start`, `venue`, `performer` each
  contribute; missing/unparseable fields subtract.
- **Model-reported confidence:** the GPT-4o importer returns a per-event confidence that is
  blended into the score.

Bands used by the dashboard and Excel report: **high ≥ 0.80 · medium 0.50–0.79 · low < 0.50**.

`confidence` and `confidence_reason` are **excluded from the change signature**, so a score
change alone does not mark an event as Changed. Legacy rows will be backfilled with a
computed default when the v2 migration runs.

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

Helper: `get_schema_version(path)` returns the current `user_version` without running any
migration.
