# Migration Plan: Converge to a Single Architecture

**Status:** Approved (with amendments) · **Provider decision:** OpenAI (confirmed) · Code not yet modified.

This document is the agreed plan to converge the repository's two overlapping
generations (the `app/` package pipeline and the root-level standalone scripts)
into a single architecture, without losing any current capability.

---

## Approved amendments

1. **AI provider = OpenAI.** GPT-4o vision ingestion is canonical; the Anthropic
   path in `process_inbox.py` is dropped (its *normalization* logic still migrates).
2. **Confidence-scoring layer on every event.** New component `app/normalize/confidence.py`,
   applied in the normalization pass so every event from every source carries a score.
3. **No new crawler until Phase 1 is complete.** `sowal.py` stays dormant/unregistered;
   no new ingestion sources are added during Phases 1–2. Crawler work resumes only
   after Phase 3, gated on Phase 1 being done.

---

## Target end-state

One backbone: the `app/` package, driven by `run_monitor.py`, publishing to `docs/`.

```
run_monitor.py  →  app.monitor.run_pipeline()
                     ├─ crawlers/      framework + registry            ← ingestion source type
                     ├─ images/        one vision importer + OCR        ← ingestion source type
                     ├─ normalize/     dedup + name/time canonicalize
                     │                 + confidence scoring             ← NEW home for scattered logic
                     ├─ database/      data/events.db (the only DB)
                     ├─ reconcile/     the only diff engine
                     ├─ excel/         Excel report
                     └─ dashboard/     renders docs/index.html          ← NEW, replaces 3 HTML builders
GitHub Actions      →  runs run_monitor.py, publishes docs/  (no cp clobber)
```

Objective mapping:
- **One ingestion pipeline** — `crawlers/` + `images/` feeding `monitor`.
- **One SQLite database** — `data/events.db` via `database/db.py`.
- **One reconciliation engine** — `reconcile/changes.py`.
- **One dashboard generator** — `dashboard/`.
- **One GitHub Pages deployment** — a single CI job publishing `docs/`.

---

## Capability inventory (nothing may be lost)

No file is deleted until its capability has a verified new home.

| #  | Capability | Current location | New home |
|----|-----------|------------------|----------|
| 1  | Crawler framework + registry | `app/crawlers/` | kept as-is |
| 2  | Seed fallback events | `registry.SeedCrawler` | kept |
| 3  | SoWal scraper (orphaned) | `app/crawlers/sowal.py` | kept, registered later (after Phase 3) |
| 4  | Vision image ingestion (GPT-4o) | `app/images/importer.py` | canonical importer |
| 5  | Alt-provider ingestion (Anthropic) | `process_inbox.py` | dropped (provider); normalization migrates |
| 6  | Apple Vision offline OCR | `ocr_and_rebuild.py` | migrated → `app/images/ocr.py` (optional) |
| 7  | day_of_week / week_of → date | `importer._normalise` | kept |
| 8  | Name canonicalization table | `process_inbox.normalize_names` | migrated → `app/normalize/` |
| 9  | Time normalization + venue default times | `process_inbox.normalize_names` | migrated → `app/normalize/` |
| 10 | Dedup | `monitor._normalise_events` | kept, folded into `app/normalize/` |
| 11 | SQLite storage + run history | `app/database/db.py` | canonical |
| 12 | Run-to-run reconciliation | `app/reconcile/changes.py` | canonical |
| 13 | Excel 4-sheet report | `app/excel/exporter.py` | kept (gains Confidence column) |
| 14 | Rich HTML dashboard (Tonight card, map, search/sort/filter) | hand-built `docs/index.html` | templatized → `app/dashboard/` |
| 15 | Local preview server | `run.sh` | kept (fix broken path) |
| 16 | GitHub Pages deploy | `.github/workflows/update-events.yml` | rewritten |
| 17 | Watchlist data | `artists.txt`, `venues.txt` | kept |
| 18 | **Confidence scoring (NEW)** | — | `app/normalize/confidence.py` |

---

## What to KEEP

- `run_monitor.py` — the single entry point.
- The entire `app/` package: `monitor.py`, `database/db.py`, `reconcile/changes.py`,
  `excel/exporter.py`, `crawlers/` (framework, registry, `sowal.py`), `images/importer.py`.
- `tests/` — extend, don't replace.
- `data/events.db` — the one database.
- `docs/index.html` — kept as the design source of truth, then converted into a template
  (its hand-maintained layout becomes the generator's template; content is not discarded).
- `artists.txt`, `venues.txt`, `README.md` (updated), `requirements.txt` (updated).

## What to DELETE (only after its capability is migrated and verified)

- `process_inbox.py` — after normalization logic (#8, #9) is ported into `app/`.
- `ocr_and_rebuild.py` — after OCR parser (#6) is ported; its inline dashboard builder is
  dropped (superseded by `app/dashboard/`).
- `data/template.html`, `data/index.html` — redundant duplicates; dashboard is generated
  into `docs/` only.
- `docs/template.html` (74-line stub that cannot produce the real dashboard), `docs/events.html` (stale).
- The CI `cp data/index.html docs/index.html` step — an active bug that overwrites the rich
  dashboard with the simpler generated one.

## What to MIGRATE

1. **Normalization logic** → new `app/normalize/` module: dedup (from `monitor`), the
   name/venue canonicalization table + time-format conversion + venue default times
   (from `process_inbox.py`). Wired into `monitor.run_pipeline()` as one normalization pass.
2. **Apple Vision OCR** → `app/images/ocr.py`: the positional two-column parser, exposed as
   an optional importer (guarded so non-macOS / CI environments skip it gracefully).
3. **Rich dashboard** → `app/dashboard/`: templatize the current `docs/index.html`, then
   generate from `data/events.db` and write `docs/index.html` directly.
4. **CI workflow** → run `run_monitor.py`, commit generated `docs/index.html` + `data/events.db`,
   publish via Pages with no clobbering copy step. Secret = `OPENAI_API_KEY`.

---

## Confidence-scoring layer (Amendment 2) — design

Lives in `app/normalize/confidence.py`, applied in the normalization pass so every event —
from any source — carries a score before it is saved or reconciled.

**Score ∈ 0.0–1.0**, stored in new columns `confidence REAL` and `confidence_reason TEXT`.

Inputs:
- **Source trust (base weight):** structured crawler ≈ 0.9 · GPT-4o vision ≈ 0.8 ·
  Apple Vision OCR ≈ 0.5 · seed ≈ 0.6.
- **Field completeness:** valid ISO `date`, `time_start`, `venue`, `performer` each contribute;
  missing/unparseable fields subtract.
- **Model-reported confidence:** from Phase 3, the GPT-4o prompt returns a per-event confidence,
  blended with the above.

Bands: **high ≥ 0.80 · medium 0.50–0.79 · low < 0.50** — consumed by the dashboard
(badge + filter/sort) and Excel (column + shading).

**Reconciliation rule:** `confidence` (and `confidence_reason`) are excluded from
`_event_signature`, so a score change alone does not register as a "changed" event
(avoids noise in the Changes sheet). This is consistent with the identity model below,
which classes `confidence` as a mutable attribute.

---

## Event identity & change model (confirmed)

Confirmed during Phase 0.

- **Identity key = `performer + venue + date`.** Two events with the same artist, venue,
  and date are the same event; anything else is a distinct event.
- **Mutable attributes** (not part of identity): `time_start`, `time_end`, `stage`, `url`,
  `source`, `confidence`, `confidence_reason`.
- **Change semantics:**
  - A **time change** at the same artist/venue/date → **Changed** (not remove+new).
  - The same artist playing **two different venues** on one day → two distinct events, both preserved.
  - **`confidence` / `source` / `url`** changes alone → **not** classed as Changed
    (excluded from `_event_signature`).

**Code impact (implemented in Phase 2):** add `venue` to `_event_key` in
`app/reconcile/changes.py`. `_event_signature` already includes `time_start`/`time_end`/`stage`
and already excludes `url`/`source`/`confidence`, so no signature change is required beyond
keeping `confidence` out.

**Baseline note:** `tests/test_pipeline.py::test_compare_changed` currently **fails** (8/9 pass)
because the present key omits `venue` and *includes* `time`, so a time change reads as
remove+new. The Phase 2 key change resolves this and turns the baseline green. Recorded here
as a known, decided baseline failure rather than fixed in Phase 0 (which is non-mutating to source).

---

## Migration order

Each phase leaves the repo runnable with tests green. No deletion happens until the
replacement is verified.

### Phase 0 — Safety net *(no source changes)*
Commit current state on the working branch; run `pytest` to record a green baseline;
back up `data/events.db`; provider decision confirmed (OpenAI).

### Phase 1 — Database convergence
Make `app/database/db.py` the only writer. Confirm its schema is a superset of the
root-script schema (it is: adds `stage`, `source`, `run_id`). Add additive columns
`confidence REAL` and `confidence_reason TEXT`; backfill legacy rows with a computed
default. One-time migration/guard for rows lacking `run_id`. Provider-independent;
verify tests + a dry pipeline run.

**Gate: no new crawler work may begin until Phase 1 is verified complete (Amendment 3).**

### Phase 2 — Normalization + confidence convergence
Create `app/normalize/`, port the canonicalization/time/venue-default logic, fold in
existing dedup, and add `confidence.py`. Wire into `monitor`. Add a `Confidence` column
to the Excel exporter. Unit-test normalization and the scorer. Nothing deleted yet.

Also implement the confirmed **event-identity model**: add `venue` to `_event_key` in
`app/reconcile/changes.py` (identity = performer + venue + date). This turns the known
baseline failure (`test_compare_changed`) green.

### Phase 3 — Ingestion & provider convergence (OpenAI)
Make `app/images/importer.py` the single vision importer on GPT-4o; extend its prompt to
return per-event confidence for the scorer. Port Apple Vision OCR to `app/images/ocr.py`
as an optional source (lower base trust). Update `requirements.txt`. Verify image
ingestion end-to-end with a sample screenshot.

*After this phase, new crawler work (including activating `sowal.py`) may resume.*

Delivered in slices: **3A** (OpenAI Vision screenshot importer + provider convergence),
**3B** (SoWal activated, normalized, reconciliation verified), plus the CrawlPolicy
strategy split and production policy. Apple Vision OCR (`app/images/ocr.py`) remains a
later slice.

### Phase 3C — Source provenance
One event = many observations. `normalize_events` now GROUPS sightings by identity instead
of dropping duplicates. Adds the `event_sources` table (v3) and provenance columns on
`events` (`source_count`, `verification_count`, `conflict_flag`, `conflict_reason`).

Confidence is two-dimensional per observation (`source_confidence`, `extraction_confidence`)
and aggregated per event by a dedicated **`ConfidenceAggregator`** (hybrid: start from the
best observation, add diminishing-returns corroboration for agreeing independent sources,
apply a conflict penalty, cap at 0.99). Conflicts on a mutable field (time/stage) set
`conflict_flag` + `conflict_reason`.

**Architectural invariant (enforced from Phase 4 on):** the dashboard is *dumb*. The
pipeline persists everything renderable (observations, aggregate confidence, conflict
flag/reason); the dashboard NEVER computes confidence, reconciliation, venue defaults, or
canonical names — it only renders.

### Phase 4 — Dashboard convergence
Build `app/dashboard/` by templatizing the existing rich `docs/index.html`; generate from
the DB; wire into `run_pipeline()`. Surface confidence (badges, sort/filter by band).
**Verify the generated `docs/index.html` matches the current live dashboard visually**
(Tonight card, map, search/sort/filter) before proceeding.

### Phase 5 — Delete redundancies
With every capability proven in `app/`, remove `process_inbox.py`, `ocr_and_rebuild.py`,
`data/template.html`, `data/index.html`, `docs/template.html`, `docs/events.html`.
Fix the `run.sh` path.

### Phase 6 — CI convergence
Rewrite the workflow to run `run_monitor.py` and publish `docs/` directly (remove the
clobbering `cp`), using `OPENAI_API_KEY`.

### Phase 7 — Docs & final verification
Update `README.md` for the single architecture; reconcile `requirements.txt`; run full
`pytest` + one clean end-to-end pipeline run; confirm Pages output.

---

## Key risks

- **Dashboard regression (Phase 4):** the live `docs/index.html` is hand-maintained and
  richer than any generator currently produces — templatizing it faithfully is the
  highest-risk step. Gated on a visual match.
- **CI `cp` bug:** currently degrades the deployed dashboard silently; fixed in Phase 6.
- **`sowal.py`:** left registered-but-dormant; convergence must not delete it, and it is
  not activated until after Phase 3.
