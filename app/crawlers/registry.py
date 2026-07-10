from app.crawlers.sowal import SoWalCrawler
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

from app.crawlers.sowal import SoWalCrawler

ALL_CRAWLERS = [
    SeedCrawler(),
    SoWalCrawler(),
    AJsGraytonCrawler(),
    ChiringoCrawler(),
    ShunkGulleyCrawler(),
]


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
