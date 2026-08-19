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
    backfill_venue_default_times,
    detect_schedule_conflicts,
    init_db,
    load_events,
    normalize_stored_times,
    purge_dateless_events,
    purge_non_music_events,
    purge_past_events,
    recanonicalize_performers,
    recanonicalize_venues,
    record_run,
    resolve_sowal_conflicts,
    resolve_stale_image_relistings,
    resolve_stale_url_relistings,
    upsert_events,
)
from app.excel.exporter import generate_report
from app.images import ingest_inbox
from app.images.importer import INBOX_DIR, SUPPORTED_EXTS
from app.normalize import normalize_events

logger = logging.getLogger(__name__)


def resolve_and_finalize(run_id: str, changes: dict) -> dict:
    """
    Shared back half of the pipeline, run after ANY batch of new
    observations lands -- a full crawl run or a standalone inbox-only run
    (see run_inbox_only()) -- so the two entry points can't drift out of
    sync the way three separate hand-copies of this chain already have
    (Shunk Gulley, North Beach Social x2). Order matters: conflict
    resolution before recanonicalization, since a rename can newly collide
    two rows that conflict resolution should have already sorted out.

    Returns the same per-step stats dict run_pipeline() has always
    returned in its "result", plus dashboard_path/report_path.
    """
    # Policy: when a venue's own site crawler and the sowal aggregator
    # report different performers at the exact same (venue, date, time),
    # that's one real slot described two ways, not two bookings -- the
    # site's own data wins and the sowal-only event is dropped.
    conflict_result = resolve_sowal_conflicts()

    # Policy: when the SAME source re-describes the exact same listing
    # (source + url + date all match) with a different performer string
    # across two runs -- a placeholder got filled in, an "at Venue" suffix
    # got trimmed, or the site changed the act -- keep only the most
    # recently observed version (confirmed live 2026-08-07: Shunk Gulley's
    # Tockify feed swapped "Chris Johnson" for "David Dunavent" on the same
    # slot/detail-link between two crawls).
    relisting_result = resolve_stale_url_relistings()

    # Same rule of thumb, for venue screenshots instead of a stable url:
    # when a venue's own flyer graphic gets re-captured and disagrees with
    # an earlier capture of the same slot, trust the more recent screenshot
    # (confirmed live 2026-08-07: North Beach Social edited "12 Eleven" +
    # "Tbd" to "Cadillac Willy" + "The Typos" on its own Instagram post
    # between two captures).
    image_relisting_result = resolve_stale_image_relistings()

    # Retroactive re-canonicalization: CANONICAL_FIXES and the all-caps
    # fold only apply to events ingested AFTER a fix exists, so a spelling
    # variant saved by an earlier run (e.g. "NEAT" before "Neat" was
    # folded) would otherwise sit next to its canonical form forever,
    # showing up as an apparent duplicate on the dashboard. Safe to re-run
    # every time -- a no-op once names have converged.
    venue_fix = recanonicalize_venues()
    performer_fix = recanonicalize_performers()

    # A venue rename above can newly match a VENUE_DEFAULT_TIMES entry (e.g.
    # "PapaSurf" -> "Papa Surf"), but only an event that merged with a
    # same-date counterpart inherits a time via recompute_aggregates(); a
    # standalone renamed event stays blank otherwise (confirmed live
    # 2026-08-08 -- see backfill_venue_default_times()). Safe to re-run.
    backfilled_times = backfill_venue_default_times()

    # Reformat every time_start/time_end to canonical "H:MM AM/PM" (or a
    # range), including splitting the default just backfilled above into a
    # proper start/end pair. Safe to re-run.
    times_normalized = normalize_stored_times()

    # Retroactive cleanup for non-music community-calendar listings (county
    # fairs, air shows, wine tastings, ...) that were saved before
    # detect_non_music() learned their pattern. Safe to re-run every time;
    # only ever deletes rows the current patterns match.
    purged_non_music = purge_non_music_events()

    # Catch any pre-existing dateless row build_observation()'s ISO-date
    # guard wouldn't have caught (it only applies to events ingested after
    # 2026-08-09) -- purge_past_events()'s `date < cutoff` never matches a
    # NULL/non-comparable date, so this needs its own pass.
    purged_dateless = purge_dateless_events()

    # Drop events whose date has already passed. Unlike "removed" above,
    # this isn't inferred from crawl absence — a past date is a fact, not
    # a guess — so it's safe to delete outright rather than just hide.
    purged_past = purge_past_events()

    # Read-only scan for the "same flyer misread two different ways" bug
    # class (confirmed 2026-08-02 on Red Fish Taco and Shelby's Beach Bar)
    # -- surfaces candidates in the log instead of requiring a manual
    # eyeball-every-venue review after each run. Never modifies data; a
    # real misread still needs a human to read the actual flyer and
    # correct it (see detect_schedule_conflicts()).
    schedule_conflicts = detect_schedule_conflicts()
    if schedule_conflicts:
        logger.warning("Found %d possible schedule conflict(s):", len(schedule_conflicts))
        for f in schedule_conflicts:
            if f["type"] == "same_night_collision":
                logger.warning(
                    "  [same night] %s on %s: %s",
                    f["venue"], f["date"], " vs ".join(f["performers"]),
                )
            else:
                logger.warning(
                    "  [irregular recurrence] %s at %s: %s",
                    f["performer"], f["venue"], ", ".join(f["dates"]),
                )
    else:
        logger.info("No schedule conflicts found")

    # Dashboard first, Excel second, each independently best-effort: a
    # failure in either (e.g. a transient iCloud file-provider I/O error
    # under ~/Documents -- confirmed live 2026-08-08, openpyxl's import hit
    # "OSError: [Errno 11] Resource deadlock avoided" 4 seconds into an
    # active brctl sync window) must never cost the OTHER artifact, and
    # must never abort the run before it -- the actual event data is
    # already committed to the db by this point, so losing just the report
    # generation is far better than the whole run dying and leaving the
    # dashboard stale too.
    all_events = load_events()          # canonical events (one row per identity)
    try:
        from app.dashboard.render import generate as generate_dashboard
        dashboard_path = generate_dashboard()
    except Exception as exc:
        logger.warning("Dashboard generation failed: %s", exc)
        dashboard_path = None

    try:
        report_path = generate_report(all_events, changes, run_id)
    except Exception as exc:
        logger.warning("Excel report generation failed: %s", exc)
        report_path = None

    return {
        "sowal_conflicts_resolved": conflict_result["events_deleted"],
        "url_relistings_resolved": relisting_result["events_deleted"],
        "image_relistings_resolved": image_relisting_result["events_deleted"],
        "venues_renamed":     venue_fix["renamed"],
        "venues_merged":      venue_fix["merged"],
        "performers_renamed": performer_fix["renamed"],
        "performers_merged":  performer_fix["merged"],
        "backfilled_times":   backfilled_times,
        "times_normalized":   times_normalized,
        "purged_non_music": purged_non_music,
        "purged_dateless": purged_dateless,
        "purged_past":     purged_past,
        "schedule_conflicts": len(schedule_conflicts),
        "report_path":     str(report_path) if report_path else None,
        "dashboard_path":  str(dashboard_path) if dashboard_path else None,
    }


def run_inbox_only(run_id: str | None = None) -> dict:
    """
    Lean entry point for scripts/process_inbox.py: process whatever's
    sitting in images/inbox/ right now and finalize, skipping the (slow,
    network-heavy) crawler step entirely. Meant to run in a few seconds to
    a couple minutes -- fast enough to fire from a folder watcher every
    time a screenshot lands, rather than waiting for the once-daily full
    pipeline.
    """
    run_id = run_id or datetime.now().strftime("%Y%m%d_%H%M%S") + "_inbox"
    logger.info("=" * 60)
    logger.info("Inbox-only run ID: %s", run_id)

    init_db()

    inbox_images = [
        f for f in INBOX_DIR.iterdir()
        if INBOX_DIR.exists() and f.suffix.lower() in SUPPORTED_EXTS
    ] if INBOX_DIR.exists() else []
    if not inbox_images:
        logger.info("No images in inbox — nothing to do")
        return {"run_id": run_id, "image_files": 0, "image_events": 0}

    image_events = ingest_inbox()   # GPT-4o Vision, else Apple Vision OCR
    logger.info("Images processed: %d files, %d events", len(inbox_images), len(image_events))

    normalised = normalize_events(image_events)
    result = upsert_events(normalised, run_id=run_id)
    record_run(run_id=run_id, events_saved=result["saved"])

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

    finalized = resolve_and_finalize(run_id, changes)

    from app.reconcile.changelog import write_changelog
    changelog_path = write_changelog(run_id, changes)

    out = {
        "run_id":          run_id,
        "image_files":     len(inbox_images),
        "image_events":    len(image_events),
        "events_saved":    result["saved"],
        "new_or_changed":  changes["summary"]["total_delta"],
        "changes":         changes,
        "changelog_path":  str(changelog_path) if changelog_path else None,
        **finalized,
    }
    logger.info("Inbox-only run complete. %s", out)
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
    logger.info("Step 2/6 — Processing image inbox")
    inbox_images = [
        f for f in INBOX_DIR.iterdir()
        if INBOX_DIR.exists() and f.suffix.lower() in SUPPORTED_EXTS
    ] if INBOX_DIR.exists() else []

    image_events = ingest_inbox()   # GPT-4o Vision, else Apple Vision OCR
    logger.info("Images processed: %d files, %d events", len(inbox_images), len(image_events))

    # 4. Combine + normalise
    logger.info("Step 3/6 — Normalising events")
    all_raw    = crawler_events + image_events
    normalised = normalize_events(all_raw)
    logger.info("Normalised event count: %d", len(normalised))

    # 5. Upsert by identity — observations accumulate onto existing events, so a
    #    second source corroborates rather than creating a duplicate.
    logger.info("Step 4/6 — Upserting events (accumulating observations)")
    result = upsert_events(normalised, run_id=run_id)
    saved  = result["saved"]
    record_run(run_id=run_id, events_saved=saved)

    # 6. Changes come from the upsert itself.
    #    NOTE: a run is a PARTIAL view (one crawl), not a full snapshot of reality,
    #    so a source simply not re-observing an event does NOT mean it was removed.
    #    Removal is therefore never inferred here.
    logger.info("Step 5/6 — Reconciling")
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

    # 6b. Push a notification for new/changed favorites_watch findings
    #    specifically (not just any new/changed event at a favorite venue --
    #    a live run confirmed that's far too broad, see pipeline_notify.py).
    #    Still reuses this same new/changed result rather than a separate
    #    diffing pass, so there's one source of truth for "did anything
    #    change." Best-effort: a notification failure (missing NTFY_TOPIC,
    #    ntfy.sh down, ...) should never fail the whole pipeline run.
    try:
        from app.favorites_watch.pipeline_notify import notify_favorites_changes
        notify_favorites_changes(changes)
    except Exception as exc:
        logger.warning("Favorites notification step failed: %s", exc)

    # 7-13. Conflict resolution, recanonicalization, purging, schedule-conflict
    #    scan, Excel + dashboard generation — shared with run_inbox_only(),
    #    see resolve_and_finalize().
    logger.info("Step 6/6 — Resolving conflicts, recanonicalizing, purging, publishing")
    finalized = resolve_and_finalize(run_id, changes)

    from app.reconcile.changelog import write_changelog
    changelog_path = write_changelog(run_id, changes)
    logger.info(
        "Sowal conflicts: %d · URL relistings: %d · Image relistings: %d · "
        "Venues renamed/merged: %d/%d · Performers renamed/merged: %d/%d · "
        "Backfilled times: %d · Times reformatted: %d · "
        "Purged non-music/dateless/past: %d/%d/%d · Schedule conflicts: %d",
        finalized["sowal_conflicts_resolved"], finalized["url_relistings_resolved"],
        finalized["image_relistings_resolved"],
        finalized["venues_renamed"], finalized["venues_merged"],
        finalized["performers_renamed"], finalized["performers_merged"],
        finalized["backfilled_times"], finalized["times_normalized"],
        finalized["purged_non_music"], finalized["purged_dateless"], finalized["purged_past"],
        finalized["schedule_conflicts"],
    )

    result = {
        "run_id":          run_id,
        "crawler_events":  len(crawler_events),
        "image_files":     len(inbox_images),
        "image_events":    len(image_events),
        "events_saved":    saved,
        "new_or_changed":  changes["summary"]["total_delta"],
        "changes":         changes,
        "changelog_path":  str(changelog_path) if changelog_path else None,
        **finalized,
    }

    logger.info("Pipeline complete. %s", result)
    return result
