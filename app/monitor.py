"""
app/monitor.py
Pipeline orchestrator — called by run_monitor.py.
Returns a result dict consumed by the terminal summary printer.
"""
import logging
from datetime import datetime
from pathlib import Path

from app.crawlers.registry import run_all_crawlers
from app.database.db import (
    get_last_run_id,
    init_db,
    load_events,
    record_run,
    save_events,
)
from app.excel.exporter import generate_report
from app.images.importer import INBOX_DIR, SUPPORTED_EXTS, process_inbox
from app.reconcile.changes import compare_runs

logger = logging.getLogger(__name__)


def _normalise_events(events: list[dict]) -> list[dict]:
    """
    Light normalisation pass — deduplicate and fill missing name field.
    """
    seen = set()
    out  = []
    for ev in events:
        performer = (ev.get("performer") or "").strip()
        venue     = (ev.get("venue") or "").strip()
        date      = (ev.get("date") or "").strip()
        time      = (ev.get("time_start") or "").strip().upper()

        if not performer:
            continue

        key = f"{performer.lower()}|{venue.lower()}|{date}|{time}"
        if key in seen:
            continue
        seen.add(key)

        if not ev.get("name"):
            ev["name"] = f"{performer} at {venue}" if venue else performer

        out.append(ev)
    return out


def run_pipeline() -> dict:
    """
    Execute the full monitoring pipeline.
    Returns a result dict with keys used by the terminal summary.
    """
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    logger.info("=" * 60)
    logger.info("Run ID: %s", run_id)

    # 1. Init DB
    init_db()

    # 2. Run crawlers
    logger.info("Step 1/6 — Running crawlers")
    crawler_events = run_all_crawlers()
    logger.info("Crawlers returned %d events", len(crawler_events))

    # 3. Image inbox
    logger.info("Step 2/6 — Processing image inbox")
    inbox_images = [
        f for f in INBOX_DIR.iterdir()
        if INBOX_DIR.exists() and f.suffix.lower() in SUPPORTED_EXTS
    ] if INBOX_DIR.exists() else []

    image_events = process_inbox()
    logger.info("Images processed: %d files, %d events", len(inbox_images), len(image_events))

    # 4. Combine + normalise
    logger.info("Step 3/6 — Normalising events")
    all_raw    = crawler_events + image_events
    normalised = _normalise_events(all_raw)
    logger.info("Normalised event count: %d", len(normalised))

    # 5. Load previous run for comparison
    logger.info("Step 4/6 — Loading previous run for comparison")
    prev_run_id    = get_last_run_id()
    previous_events = load_events(run_id=prev_run_id) if prev_run_id else []
    logger.info("Previous run '%s' had %d events", prev_run_id, len(previous_events))

    # 6. Save to DB
    logger.info("Step 5/6 — Saving events to SQLite")
    for ev in normalised:
        ev["source"] = ev.get("source") or "crawler"
    saved = save_events(normalised, run_id=run_id)
    record_run(run_id=run_id, events_saved=saved)

    # 7. Compare runs
    changes = compare_runs(normalised, previous_events)

    # 8. Generate Excel
    logger.info("Step 6/6 — Generating Excel report")
    all_events  = load_events()          # full history for the report
    report_path = generate_report(normalised, changes, run_id)

    result = {
        "run_id":          run_id,
        "crawler_events":  len(crawler_events),
        "image_files":     len(inbox_images),
        "image_events":    len(image_events),
        "events_saved":    saved,
        "new_or_changed":  changes["summary"]["total_delta"],
        "report_path":     str(report_path),
        "changes":         changes,
    }

    logger.info("Pipeline complete. %s", result)
    return result
