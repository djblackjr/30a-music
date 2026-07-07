# 🎵 30A Music Intelligence

Local music event monitoring for the 30A / South Walton area.
Crawls venues, ingests Instagram/calendar screenshots via GPT-4o Vision,
saves events to SQLite, detects changes run-to-run, and exports an Excel report.

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Add your OpenAI key
cp .env.example .env
# edit .env and paste your key

# 3. Drop schedule screenshots into the inbox
cp ~/Downloads/AJs_schedule.png images/inbox/

# 4. Run the monitor
python run_monitor.py
```

**Terminal output:**

```
30A Music Intelligence — Run Monitor
------------------------------------
  Crawler events found:   2
  Image files found:      1
  Events saved:           9
  New or changed events:  9
  Excel report:           exports/30A_Live_Music_Report.xlsx
```

---

## Project Structure

```
30a-music-intel/
├── run_monitor.py              ← main entry point
├── run.sh                      ← start the HTML dashboard server
├── ocr_and_rebuild.py          ← original Apple Vision OCR script (unchanged)
├── requirements.txt
├── .env.example
│
├── app/
│   ├── monitor.py              ← pipeline orchestrator
│   ├── crawlers/
│   │   └── registry.py         ← crawler registry (add new crawlers here)
│   ├── database/
│   │   └── db.py               ← SQLite helpers
│   ├── excel/
│   │   └── exporter.py         ← Excel report generator (4 sheets)
│   ├── images/
│   │   └── importer.py         ← GPT-4o Vision image ingestion
│   └── reconcile/
│       └── changes.py          ← run-to-run diff engine
│
├── images/
│   ├── inbox/                  ← drop screenshots here before running
│   ├── processed/              ← moved here after successful processing
│   └── failed/                 ← moved here if GPT-4o fails
│
├── data/
│   ├── events.db               ← SQLite database
│   └── index.html              ← HTML dashboard (served by run.sh)
│
├── exports/
│   └── 30A_Live_Music_Report.xlsx
│
├── logs/
│   └── run_monitor.log
│
└── tests/
    └── test_pipeline.py
```

---

## Image Ingestion (GPT-4o Vision)

Drop any PNG, JPG, or JPEG into `images/inbox/` before running.

GPT-4o Vision reads the image and returns structured JSON — no regex needed.
It handles:
- Multi-column weekly grids (like AJ's Grayton)
- Instagram flyers with stylised fonts
- Calendar screenshots
- Partial or low-contrast text

Processed images move to `images/processed/`. Failed images move to `images/failed/`.

---

## Excel Report

Four sheets are generated in `exports/30A_Live_Music_Report.xlsx`:

| Sheet | Contents |
|-------|----------|
| All Events | Every event from this run, sorted by date |
| Upcoming | Future events only |
| Changes | New / changed / removed vs previous run |
| By Venue | Events grouped by venue |

---

## Adding Crawlers

Open `app/crawlers/registry.py` and add a new class:

```python
class MyVenueCrawler(BaseCrawler):
    name = "my_venue"

    def fetch(self) -> list[dict]:
        # fetch and return list of event dicts
        return []
```

Then register it:

```python
ALL_CRAWLERS = [
    SeedCrawler(),
    MyVenueCrawler(),   # ← add here
    ...
]
```

Event dicts should have these keys (all optional except `performer`):
- `performer` — artist name
- `name` — display name (auto-generated if missing)
- `date` — ISO 8601 string `YYYY-MM-DD`
- `time_start` — e.g. `"6PM"`, `"8:30PM"`
- `time_end`
- `venue`
- `stage`
- `url`
- `source`

---

## HTML Dashboard

The original dashboard still works:

```bash
bash run.sh
# → open http://localhost:8080
```

To rebuild it after a monitor run:

```bash
python ocr_and_rebuild.py
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
| `OPENAI_API_KEY` | Yes (for images) | GPT-4o Vision API key |

Set in `.env` (copied from `.env.example`). Never commit `.env`.

---

## Suggested Workflow

```
Weekly:
  1. Download schedule screenshots from Instagram / venue sites
  2. cp screenshots ~/30a-music-intel/images/inbox/
  3. python run_monitor.py
  4. Open exports/30A_Live_Music_Report.xlsx
  5. bash run.sh  →  check dashboard at localhost:8080
```
# 30a-music
