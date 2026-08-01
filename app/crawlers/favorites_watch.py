"""
app/crawlers/favorites_watch.py
Wraps app.favorites_watch.research as a normal crawler: one OpenAI
web_search call per favorite venue/artist (app/dashboard/venue_groups.csv,
artists.csv), looking for a specific, dated, sourced upcoming show. Once
converted to this raw-event-dict shape, it flows through the exact same
normalize/reconcile/confidence pipeline as every other crawler -- there's
no separate favorites-only database or dashboard path.

Findings without a real performer name (venue-only Tier 1 hits like "a
special one-off show at this venue") fall back to the venue's own name as
the performer field rather than being dropped, so they still show up
somewhere on the dashboard instead of silently disappearing.
"""
import logging
from datetime import date

logger = logging.getLogger(__name__)

MONDAY = 0


class FavoritesWatchCrawler:
    name = "favorites_watch"

    def fetch(self) -> list[dict]:
        # Runs weekly instead of daily -- one OpenAI web_search call per
        # favorite, every day, was the primary driver of this project's
        # OpenAI cost (confirmed 2026-08-01). The main pipeline itself still
        # runs daily via cron-job.org for the free scrapers (SoWal, AJ's
        # Grayton, ...); this crawler just no-ops on every day but Monday.
        if date.today().weekday() != MONDAY:
            logger.info("[FavoritesWatchCrawler] skipped -- runs Monday mornings only")
            return []

        from app.dashboard.render import _favorite_performer_names, _favorite_venue_names
        from app.favorites_watch.research import research_all_favorites

        venue_names = _favorite_venue_names()
        performer_names = _favorite_performer_names()
        findings = research_all_favorites(venue_names, performer_names)

        events = []
        for f in findings:
            performer = f.get("performer") or f["favorite_name"]
            venue = f.get("venue") or f["favorite_name"]
            events.append({
                "name": f"{performer} at {venue}",
                "performer": performer,
                "venue": venue,
                "date": f["date"],
                "time_start": f.get("time"),
                "time_end": None,
                "stage": None,
                "url": f["source_url"],
                "source": "favorites_watch",
                "observation_type": "ai_web_search",
            })
        return events
