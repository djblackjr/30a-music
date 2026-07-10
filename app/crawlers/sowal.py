"""
SoWal event crawler.

Responsible only for discovering live music events from SoWal.

Returns normalized event dictionaries.

Does not write databases.
Does not generate HTML.
Does not reconcile events.
"""

import logging

from app.crawlers.registry import BaseCrawler

logger = logging.getLogger(__name__)


class SoWalCrawler(BaseCrawler):
    """Crawler for public SoWal event listings."""

    name = "sowal"

    BASE_URL = "https://sowal.com/events"

    def crawl(self):
        """
        Crawl SoWal and return normalized event dictionaries.

        Version 1 intentionally returns no events until
        the crawler is implemented.
        """

        logger.info("SoWal crawler starting")

        return []
