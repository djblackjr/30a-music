"""
app/crawlers/registry.py
Crawler registry — add new crawlers here and they auto-run in the pipeline.
"""
import logging
from datetime import date, datetime
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class BaseCrawler:
    name: str = "base"

    def fetch(self) -> list[dict]:
        """Return a list of raw event dicts."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Seed / fallback crawler (preserves existing behaviour)
# ---------------------------------------------------------------------------

class SeedCrawler(BaseCrawler):
    """
    Returns a small set of hard-coded seed events so the pipeline always has
    something to work with, even when no images or live crawlers are available.
    Mirrors the original fallback data from ocr_and_rebuild.py.
    """
    name = "seed"

    SEED_EVENTS = [
        {
            "name": "Stevie Monce at Chiringo",
            "date": date.today().isoformat(),
            "time_start": "6PM",
            "time_end": None,
            "venue": "Chiringo",
            "performer": "Stevie Monce",
            "url": "https://instagram.com/steviemonce",
            "stage": None,
            "source": "seed",
        },
        {
            "name": "Casey Kearney at Shunk Gulley",
            "date": date.today().isoformat(),
            "time_start": "7PM",
            "time_end": None,
            "venue": "Shunk Gulley",
            "performer": "Casey Kearney",
            "url": "https://instagram.com/caseykearney",
            "stage": None,
            "source": "seed",
        },
    ]

    def fetch(self) -> list[dict]:
        logger.info("[SeedCrawler] returning %d seed events", len(self.SEED_EVENTS))
        return list(self.SEED_EVENTS)


# ---------------------------------------------------------------------------
# Venue website crawlers (stubs — add real scrapers here)
# ---------------------------------------------------------------------------

class AJsGraytonCrawler(BaseCrawler):
    """Crawl AJ's Grayton Beach website for live events."""
    name = "ajs_grayton"
    URL = "https://www.ajsgraytonbeach.com/entertainment"

    def fetch(self) -> list[dict]:
        # TODO: implement HTTP scraping with requests + BeautifulSoup
        logger.info("[AJsGraytonCrawler] stub — no live scraping yet")
        return []


class ChiringoCrawler(BaseCrawler):
    """Crawl Chiringo website for live events."""
    name = "chiringo"
    URL = "https://www.chiringofl.com"

    def fetch(self) -> list[dict]:
        # TODO: implement HTTP scraping with requests + BeautifulSoup
        logger.info("[ChiringoCrawler] stub — no live scraping yet")
        return []


class ShunkGulleyCrawler(BaseCrawler):
    """Crawl Shunk Gulley for live events."""
    name = "shunk_gulley"
    URL = "https://www.shunkgulley.com"

    def fetch(self) -> list[dict]:
        # TODO: implement HTTP scraping with requests + BeautifulSoup
        logger.info("[ShunkGulleyCrawler] stub — no live scraping yet")
        return []


# ---------------------------------------------------------------------------
# Registry — add every crawler you want to run here
# ---------------------------------------------------------------------------

ALL_CRAWLERS: list[BaseCrawler] = [
    SeedCrawler(),
    AJsGraytonCrawler(),
    ChiringoCrawler(),
    ShunkGulleyCrawler(),
]

# Production crawl strategy (see app/crawlers/policy.py). Bounded on purpose:
# 100 events far exceeds a ~14-day horizon, a 500+ request run every execution
# is unnecessary load on SoWal, and total crawl time must stay reasonable as
# more sources (Facebook, venue sites, artist pages, Bandsintown) are added.
# The crawler itself stays unopinionated; strategy is injected here.
# TODO (future): select Development / Production / Deep Scan by run context via a
# scheduler — see the TODO in app/crawlers/policy.py. Not implemented yet.
try:
    from app.crawlers.policy import CrawlPolicy

    SOWAL_POLICY = CrawlPolicy(
        max_events=100,
        request_delay=0.75,
    )
except Exception as exc:  # pragma: no cover — policy import should not fail
    logger.warning("[registry] CrawlPolicy unavailable: %s", exc)
    SOWAL_POLICY = None

# SoWal is imported defensively: it depends on requests/bs4/lxml, so a missing
# scraping dependency logs a warning and skips the crawler rather than breaking
# the whole pipeline (which imports this module).
try:
    from app.crawlers.sowal import SoWalCrawler
    ALL_CRAWLERS.append(SoWalCrawler(policy=SOWAL_POLICY))
    logger.info("[registry] SoWal crawler registered with production policy")
except Exception as exc:  # ImportError or any init failure
    logger.warning("[registry] SoWal crawler unavailable: %s", exc)


def run_all_crawlers() -> list[dict]:
    """Run every registered crawler and return the combined event list."""
    events: list[dict] = []
    for crawler in ALL_CRAWLERS:
        try:
            result = crawler.fetch()
            logger.info("[%s] fetched %d events", crawler.name, len(result))
            events.extend(result)
        except Exception as exc:
            logger.warning("[%s] failed: %s", crawler.name, exc)
    return events
