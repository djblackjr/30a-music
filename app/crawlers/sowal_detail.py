"""
app/crawlers/sowal_detail.py
Targeted Sowal event detail crawler.

Complements the main SoWalCrawler by directly crawling specific event detail
pages that unlock extraction the main listing's calendar rows alone can't
(an embedded flyer image via GPT-4o Vision, or a prose lineup in the page's
own description) -- see SoWalCrawler._parse_flyer_image / resolve_performer.

The venues/series worth this treatment are tracked by TITLE, not URL: a
fixed node URL (e.g. ".../live-music-crackings-541") freezes to whatever
date it was created for and never updates, since SoWal creates a new node
per recurrence rather than updating one node's date in place (confirmed
live: the same node returns identical content regardless of any `?date=`
query string). Each run, _discover_urls() re-resolves every tracked title
to its current, nearest-upcoming node URL from a fresh listing crawl --
self-contained (fetches https://sowal.com/events itself, one extra
lightweight request) rather than sharing state with the main SoWalCrawler's
own run, keeping this crawler decoupled per the registry's duck-typed
crawler protocol.

Duck-typed to the crawler protocol (has `name` and `fetch()`); registered in
app/crawlers/registry.py.
"""
import logging
import time

import requests
from bs4 import BeautifulSoup

from app.crawlers.policy import CrawlPolicy
from app.crawlers.sowal import SoWalCrawler

logger = logging.getLogger(__name__)


class SoWalDetailCrawler(SoWalCrawler):
    """Targeted crawler for specific Sowal event detail pages, resolved by title."""

    name = "sowal_detail"

    # Recurring venues/series whose detail page unlocks extraction the main
    # listing's calendar rows can't (a flyer image, or a prose lineup) --
    # matched by exact title (case/whitespace-insensitive) against a fresh
    # listing crawl every run. Easily customizable; can be sourced from
    # config/env/DB.
    TITLE_PATTERNS = [
        "Live Music @ Crackings",
        "Live Music @ Old Florida Fish House",
        "30Avenue Summer Concert Series",
        "Here Comes the Sun Summer Concert Series at Rosemary Beach",
        "Baytowne Wednesday Night Concert Series",
        "Harbor Nights at HarborWalk",
    ]

    def __init__(self, policy: CrawlPolicy | None = None):
        super().__init__(policy or CrawlPolicy(request_delay=0.5))

    def fetch(self) -> list[dict]:
        """Discover each tracked title's current URL, then fetch and parse it."""
        urls = self._discover_urls()
        logger.info(
            "[SoWalDetailCrawler] Discovered %d current URL(s) for %d tracked title(s)",
            len(urls), len(self.TITLE_PATTERNS),
        )

        all_observations = []
        limited = self.policy.limit(urls)

        for i, url in enumerate(limited):
            if i and self.policy.request_delay:
                time.sleep(self.policy.request_delay)

            logger.info("[SoWalDetailCrawler] Fetching %s", url)
            observations = self.parse_event_observations(url)
            logger.info("[SoWalDetailCrawler] Parsed %d observations from %s", len(observations), url)
            all_observations.extend(observations)

        logger.info("[SoWalDetailCrawler] Total parsed %d observations", len(all_observations))
        return all_observations

    def _discover_urls(self) -> list[str]:
        """
        Resolve each entry in TITLE_PATTERNS to its current, nearest-upcoming
        node URL by matching against a fresh calendar-row crawl of the main
        listing. A pattern with no matching row this run is skipped (logged,
        not an error) -- SoWal may have renamed or retired that recurrence.
        """
        try:
            response = requests.get(self.START_URL, headers=self.HEADERS, timeout=20)
            response.raise_for_status()
        except Exception as exc:
            logger.warning("[SoWalDetailCrawler] Could not fetch listing to discover URLs: %s", exc)
            return []

        soup = BeautifulSoup(response.text, "lxml")
        rows = self._parse_calendar_rows(soup)

        wanted = {p.strip().lower(): p for p in self.TITLE_PATTERNS}
        best: dict[str, dict] = {}
        for row in rows:
            key = (row["title"] or "").strip().lower()
            pattern = wanted.get(key)
            if not pattern:
                continue
            # Earliest matching date wins -- one representative URL per
            # tracked title, same "one URL per distinct title" convention
            # the main crawler's own enrichment step already uses.
            if pattern not in best or row["date"] < best[pattern]["date"]:
                best[pattern] = row

        missing = [p for p in self.TITLE_PATTERNS if p not in best]
        if missing:
            logger.warning("[SoWalDetailCrawler] No current listing match for: %s", missing)

        return [row["url"] for row in best.values()]
