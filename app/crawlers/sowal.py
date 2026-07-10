"""
SoWal crawler.

Currently a scaffold. It integrates with the crawler framework
but intentionally returns no events until the parser is implemented.
"""

import logging

from app.crawlers.registry import BaseCrawler

logger = logging.getLogger(__name__)


class SoWalCrawler(BaseCrawler):
    name = "sowal"

    def fetch(self):
        logger.info("[SoWalCrawler] scaffold - no events yet")
        return []
