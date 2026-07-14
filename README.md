# 🎵 30A Music Intelligence

Local music event monitoring for the 30A / South Walton area. Crawls venue
sites, ingests schedule screenshots (GPT-4o Vision or free local OCR), scores
every event's confidence from corroborating sources, saves to a single SQLite
database, and generates a static HTML dashboard + Excel report.

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. (Optional) Add your OpenAI key for image ingestion
cp .env.example .env
# edit .env and paste your key — without it, image ingestion falls back to
# free local Apple Vision OCR on macOS, or is skipped entirely elsewhere

# 3. Drop schedule screenshots into the inbox
cp ~/Downloads/AJs_schedule.png images/inbox/

# 4. Run the pipeline
python run_monitor.py
```

**Terminal output:**

```
30A Music Intelligence — Run Monitor
------------------------------------
  Crawler events found:   168
  Image files found:      1
  Events saved:           401
  New or changed events:  9
  Excel report:           exports/30A_Live_Music_Report.xlsx
  Dashboard:               docs/index.html
```

---

## Architecture

One ingestion pipeline, one SQLite database, one confidence-scoring engine,
one dashboard generator:

```
run_monitor.py  →  app/monitor.py  (pipeline orchestrator)
                        │
        ┌───────────────┼────────────────────┐
        ▼               ▼                    ▼
  app/crawlers/    app/images/          app/database/
  (venue sites)     (screenshots)        (SQLite, schema-versioned)
        │               │                    │
        └───────┬───────┘                    │
                ▼                             │
        app/normalize/                        │
        (identity, canonicalization,          │
         confidence scoring)                  │
                │                              │
                └──────────► upsert_events() ──┘
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
             app/excel/      app/dashboard/    (event_observations
             exporter.py       render.py        accumulate across runs)
```

**Event identity** = performer + venue + date. Mutable attributes
(`time_start`, `time_end`, `stage`, `url`, `confidence`) update in place — a
time change is reported as *Changed*, never as remove-and-recreate.

**Provenance**: one canonical event can be backed by many *observations*
(`event_observations` table) — one row per (source, run). Every run's
observations accumulate onto the existing event rather than overwriting it, so
an event seen by both the venue's own site and a third-party calendar shows
`source_count: 2` and a higher confidence score.

**Confidence** is two-dimensional per observation
(`source_confidence × extraction_confidence`), then aggregated across all of
an event's observations by `app/normalize/confidence.py::ConfidenceAggregator`
— corroboration from independent sources raises confidence with diminishing
returns, disagreement applies a penalty, and the result is capped below 1.0.

**The dashboard is dumb.** `app/dashboard/render.py` only renders what the
pipeline already computed — confidence, provenance, and canonical names are
never decided in the templating layer.

Full schema reference: [`docs/DATABASE.md`](docs/DATABASE.md).
Migration history / design rationale: [`docs/MIGRATION.md`](docs/MIGRATION.md).

---

## Project Structure

```
30a-music/
├── run_monitor.py              ← main entry point
├── run.sh                      ← serve the generated dashboard locally
├── requirements.txt
├── .env.example
│
├── app/
│   ├── monitor.py               ← pipeline orchestrator
│   ├── crawlers/
│   │   ├── registry.py          ← active crawler registry
│   │   ├── policy.py            ← CrawlPolicy (max_events, request_delay, ...)
│   │   └── sowal.py             ← South Walton events calendar crawler
│   ├── images/
│   │   ├── __init__.py          ← ingest_inbox() — picks GPT-4o Vision or OCR
│   │   ├── importer.py          ← GPT-4o Vision (needs OPENAI_API_KEY)
│   │   └── ocr.py                ← Apple Vision OCR (macOS only, free, offline)
│   ├── normalize/
│   │   ├── provenance.py        ← event identity, observation building/merging
│   │   ├── confidence.py        ← source/extraction confidence, ConfidenceAggregator
│   │   ├── canonical.py         ← name canonicalization, venue-performer aliases
│   │   └── times.py             ← time parsing / conflict detection
│   ├── database/
│   │   └── db.py                 ← SQLite layer, schema migrations, upsert_events()
│   ├── dashboard/
│   │   ├── render.py             ← dumb HTML dashboard generator
│   │   ├── template.html         ← dashboard template
│   │   └── legacy_import.py      ← one-time import of the old hand-curated dashboard
│   ├── excel/
│   │   └── exporter.py           ← Excel report generator
│   └── reconcile/
│       └── changes.py            ← event-signature diffing (used by db.py)
│
├── images/
│   ├── inbox/                    ← drop screenshots here before running
│   ├── processed/                ← moved here after successful processing
│   └── failed/                   ← moved here if ingestion fails
│
├── data/
│   └── events.db                 ← SQLite database (schema-versioned, additive migrations)
│
├── docs/
│   ├── index.html                ← generated dashboard (served by GitHub Pages / run.sh)
│   ├── DATABASE.md               ← schema reference
│   └── MIGRATION.md              ← architecture convergence history
│
├── exports/
│   └── 30A_Live_Music_Report.xlsx
│
├── archive/                       ← retired hand-built dashboard, kept for reference only
├── logs/
└── tests/
```

---

## Image Ingestion

Drop any PNG, JPG, or JPEG into `images/inbox/` before running. `ingest_inbox()`
(`app/images/__init__.py`) picks the importer automatically:

- **`OPENAI_API_KEY` set** → GPT-4o Vision (`app/images/importer.py`). Handles
  multi-column weekly grids, stylised flyers, calendar screenshots, and
  low-contrast text. The only path CI can use. Emits a per-event
  `model_confidence`.
- **No key, macOS with `pyobjc-framework-Vision` installed** → Apple Vision
  OCR (`app/images/ocr.py`). Free and local, but a cruder two-column
  positional parser — best suited to single-artist schedule screenshots.
- **Neither available** → images are left in the inbox with a warning; no
  importer runs.

Override the choice with `IMAGE_IMPORTER=vision` or `IMAGE_IMPORTER=ocr`.

Processed images move to `images/processed/`. Failed images move to
`images/failed/`.

---

## Excel Report

Generated at `exports/30A_Live_Music_Report.xlsx` with confidence and
provenance columns alongside the event data.

---

## Adding Crawlers

Crawlers are registered in `app/crawlers/registry.py`, each with an injected
`CrawlPolicy` (`app/crawlers/policy.py`) that separates crawl *strategy*
(`max_events`, `max_pages`, `request_delay`) from crawl *implementation*:

```python
class MyVenueCrawler(BaseCrawler):
    name = "my_venue"

    def fetch(self) -> list[dict]:
        # fetch and return list of event dicts
        return []
```

```python
ALL_CRAWLERS = [
    SowalCrawler(policy=CrawlPolicy(max_events=100, request_delay=0.75)),
    MyVenueCrawler(policy=CrawlPolicy(...)),   # ← add here
]
```

Event dicts should have these keys (all optional except `performer`):
- `performer` — artist name
- `venue`
- `date` — ISO 8601 string `YYYY-MM-DD`
- `time_start` — e.g. `"6PM"`, `"8:30PM"`
- `time_end`
- `stage`
- `url`
- `source` — used to look up trust weight in `app/normalize/confidence.py::SOURCE_TRUST`
- `observation_type` — one of `website`, `image`, `ocr`, `api`, `manual`, `social`, `calendar`

---

## Dashboard

The dashboard is generated by the pipeline (`app/dashboard/render.py`) into
`docs/index.html` on every run — there is no separate rebuild step.

```bash
bash run.sh
# → open http://localhost:8080
```

---

## Tests

```bash
python -m pytest tests/ -v
```

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | No | Enables GPT-4o Vision image ingestion. Without it, image ingestion falls back to free local Apple Vision OCR on macOS, or is skipped. |
| `IMAGE_IMPORTER` | No | Force `vision` or `ocr` instead of auto-selecting. |

Set in `.env` (copied from `.env.example`). Never commit `.env`.

---

## CI / Deployment

`.github/workflows/update-events.yml` runs the pipeline on a schedule (and on
manual trigger), commits the updated `data/events.db` and `docs/index.html`,
and pushes. GitHub Pages serves `docs/` from `main`.

---

## Suggested Workflow

```
Weekly:
  1. Download schedule screenshots from Instagram / venue sites
  2. cp screenshots images/inbox/
  3. python run_monitor.py
  4. Open exports/30A_Live_Music_Report.xlsx
  5. bash run.sh  →  check dashboard at localhost:8080
```
