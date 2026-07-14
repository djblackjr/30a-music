"""
app/dashboard/legacy_import.py
One-time migration of the hand-curated docs/index.html into the database.

Each table row becomes an OBSERVATION (source='dashboard_legacy',
observation_type='manual'), so the curated events survive as first-class
provenance and the generated dashboard can reproduce the current one exactly.

This is a migration utility, not part of the recurring pipeline. Run once:
    python -m app.dashboard.legacy_import
"""
import logging
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

LEGACY_HTML = Path("docs/index.html")
LEGACY_SOURCE = "dashboard_legacy"


def parse_legacy_html(path: Path = LEGACY_HTML) -> list[dict]:
    """
    Parse the curated dashboard's table rows into raw event dicts.
    Preserves performer, venue, date, displayed time, link, and the venue's
    colour-chip class (vt-*) so the dashboard can be reproduced exactly.
    """
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "lxml")
    tbody = soup.find("tbody", id="tb")
    if not tbody:
        raise RuntimeError("Could not find <tbody id='tb'> in legacy HTML")

    events: list[dict] = []
    for tr in tbody.find_all("tr"):
        date = tr.get("data-date") or None
        venue = tr.get("data-venue") or None
        performer = tr.get("data-performer") or None
        if not performer:
            continue

        tds = tr.find_all("td")
        time_start = tds[2].get_text(strip=True) if len(tds) > 2 else None

        url = None
        vt_class = None
        if len(tds) > 3:
            span = tds[3].find("span")
            if span and span.get("class"):
                vt_class = next((c for c in span["class"] if c.startswith("vt-")), None)
        link = tr.find("a", href=True)
        if link:
            url = link["href"]

        events.append({
            "name":             performer,
            "performer":        performer,
            "venue":            venue,
            "date":             date,
            "time_start":       time_start,
            "time_end":         None,
            "stage":            None,
            "url":              url,
            "source":           LEGACY_SOURCE,
            "observation_type": "manual",
            "vt_class":         vt_class,  # informational; not persisted
        })

    logger.info("Parsed %d legacy rows from %s", len(events), path)
    return events


def venue_class_map(events: list[dict]) -> dict[str, str]:
    """Venue name -> vt-* colour class, as used by the curated dashboard."""
    mapping: dict[str, str] = {}
    for ev in events:
        v, c = ev.get("venue"), ev.get("vt_class")
        if v and c and v not in mapping:
            mapping[v] = c
    return mapping


def import_legacy(path: Path = LEGACY_HTML) -> dict:
    """
    Parse the legacy HTML, run it through the normal normalization/provenance
    pipeline, and persist it under a dedicated legacy run. Returns a summary.
    """
    from app.database.db import init_db, record_run, save_events
    from app.normalize import normalize_events

    init_db()
    raw = parse_legacy_html(path)
    events = normalize_events([{k: v for k, v in e.items() if k != "vt_class"} for e in raw])

    run_id = "legacy_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    saved = save_events(events, run_id=run_id)
    record_run(run_id=run_id, events_saved=saved)

    return {
        "run_id": run_id,
        "rows_parsed": len(raw),
        "events_saved": saved,
        "canonical_events": len(events),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    result = import_legacy()
    print(result)
