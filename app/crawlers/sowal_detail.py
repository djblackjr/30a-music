"""
app/crawlers/sowal_detail.py
Targeted Sowal event detail crawler.

Complements the main SoWalCrawler by directly crawling specific event detail pages
that may be missed or supplementary to the main events listing. Uses the same
parsing logic as SoWalCrawler but operates on a curated list of URLs.

Duck-typed to the crawler protocol (has `name` and `fetch()`); registered in
app/crawlers/registry.py.
"""
import logging
import time
from app.crawlers.sowal import SoWalCrawler
from app.crawlers.policy import CrawlPolicy

logger = logging.getLogger(__name__)


class SoWalDetailCrawler(SoWalCrawler):
    """Targeted crawler for specific Sowal event detail pages."""

    name = "sowal_detail"

    # URLs to crawl. Easily customizable; can be sourced from config/env/DB.
    DETAIL_URLS = [
        "https://sowal.com/event/live-music-crackings-541?date=2026-07-15",
        "https://sowal.com/event/live-music-old-florida-fish-house-122?date=2026-07-15",
        "https://sowal.com/event/30avenue-summer-concert-series-42?date=2026-07-15",
        "https://sowal.com/event/here-comes-the-sun-summer-concert-series-at-rosemary-beach-4?date=2026-07-15",
        "https://sowal.com/event/baytowne-wednesday-night-concert-series-52?date=2026-07-15",
        "https://sowal.com/event/30avenue-summer-concert-series-43?date=2026-07-16",
        "https://sowal.com/event/harbor-nights-at-harborwalk-1?date=2026-07-16",
    ]

    def __init__(self, policy: CrawlPolicy | None = None):
        super().__init__(policy or CrawlPolicy(request_delay=0.5))

    def fetch(self) -> list[dict]:
        """Fetch and parse all detail URLs, aggregating observations."""
        logger.info("[SoWalDetailCrawler] Crawling %d detail URLs", len(self.DETAIL_URLS))

        all_observations = []
        urls = self.policy.limit(self.DETAIL_URLS)

        for i, url in enumerate(urls):
            if i and self.policy.request_delay:
                time.sleep(self.policy.request_delay)

            logger.info("[SoWalDetailCrawler] Fetching %s", url)
            observations = self.parse_event_observations(url)
            logger.info("[SoWalDetailCrawler] Parsed %d observations from %s", len(observations), url)
            all_observations.extend(observations)

        logger.info("[SoWalDetailCrawler] Total parsed %d observations", len(all_observations))
        return all_observations
