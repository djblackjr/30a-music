"""
app/monitor.py
Pipeline orchestrator — called by run_monitor.py.
Returns a result dict consumed by the terminal summary printer.
"""
import logging
from datetime import datetime
from pathlib import Path

from app.crawlers.registry import run_all_crawlers
from app.crawlers.sowal import partition_observations
from app.database.db import (
    init_db,
    load_events,
    purge_past_events,
    record_run,
    upsert_events,
)
from app.excel.exporter import generate_report
from app.images import ingest_inbox
from app.images.importer import INBOX_DIR, SUPPORTED_EXTS
from app.normalize import normalize_events

logger = logging.getLogger(__name__)


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
    logger.info("Step 1/8 — Running crawlers")
    crawler_events = run_all_crawlers()
    logger.info("Crawlers returned %d events", len(crawler_events))

    # SoWal observations self-classify as named/unresolved/category via
    # performer_status (see app/crawlers/sowal.py) so generic listings ("Live
    # Music", "DJ Night") never get saved as if they were named artists.
    # Observations from crawlers that don't declare a classification pass
    # through untouched — this only filters what opts in to being filtered.
    classifiable = [e for e in crawler_events if "performer_status" in e]
    passthrough  = [e for e in crawler_events if "performer_status" not in e]
    if classifiable:
        partitioned = partition_observations(classifiable)
        logger.info(
            "Observation classification: %d named / %d unresolved / %d category",
            len(partitioned["named"]), len(partitioned["unresolved"]), len(partitioned["category"]),
        )
        crawler_events = passthrough + partitioned["named"]

    # 3. Image inbox
    logger.info("Step 2/8 — Processing image inbox")
    inbox_images = [
        f for f in INBOX_DIR.iterdir()
        if INBOX_DIR.exists() and f.suffix.lower() in SUPPORTED_EXTS
    ] if INBOX_DIR.exists() else []

    image_events = ingest_inbox()   # GPT-4o Vision, else Apple Vision OCR
    logger.info("Images processed: %d files, %d events", len(inbox_images), len(image_events))

    # 4. Combine + normalise
    logger.info("Step 3/8 — Normalising events")
    all_raw    = crawler_events + image_events
    normalised = normalize_events(all_raw)
    logger.info("Normalised event count: %d", len(normalised))

    # 5. Upsert by identity — observations accumulate onto existing events, so a
    #    second source corroborates rather than creating a duplicate.
    logger.info("Step 4/8 — Upserting events (accumulating observations)")
    result = upsert_events(normalised, run_id=run_id)
    saved  = result["saved"]
    record_run(run_id=run_id, events_saved=saved)

    # 6. Changes come from the upsert itself.
    #    NOTE: a run is a PARTIAL view (one crawl), not a full snapshot of reality,
    #    so a source simply not re-observing an event does NOT mean it was removed.
    #    Removal is therefore never inferred here.
    logger.info("Step 5/8 — Reconciling")
    changes = {
        "new":       result["new"],
        "changed":   result["changed"],
        "removed":   [],
        "unchanged": result["unchanged"],
        "summary": {
            "new":         len(result["new"]),
            "changed":     len(result["changed"]),
            "removed":     0,
            "unchanged":   len(result["unchanged"]),
            "total_delta": len(result["new"]) + len(result["changed"]),
        },
    }

    # 7. Drop events whose date has already passed. Unlike "removed" above,
    #    this isn't inferred from crawl absence — a past date is a fact, not
    #    a guess — so it's safe to delete outright rather than just hide.
    logger.info("Step 6/8 — Purging past events")
    purged = purge_past_events()
    logger.info("Purged %d past events", purged)

    # 8. Generate Excel
    logger.info("Step 7/8 — Generating Excel report")
    all_events  = load_events()          # canonical events (one row per identity)
    report_path = generate_report(all_events, changes, run_id)

    # 9. Generate the dashboard from current knowledge (union across runs)
    logger.info("Step 8/8 — Generating dashboard")
    try:
        from app.dashboard.render import generate as generate_dashboard
        dashboard_path = generate_dashboard()
    except Exception as exc:
        logger.warning("Dashboard generation failed: %s", exc)
        dashboard_path = None

    result = {
        "run_id":          run_id,
        "crawler_events":  len(crawler_events),
        "image_files":     len(inbox_images),
        "image_events":    len(image_events),
        "events_saved":    saved,
        "purged_past":     purged,
        "new_or_changed":  changes["summary"]["total_delta"],
        "report_path":     str(report_path),
        "dashboard_path":  str(dashboard_path) if dashboard_path else None,
        "changes":         changes,
    }

    logger.info("Pipeline complete. %s", result)
    return result
