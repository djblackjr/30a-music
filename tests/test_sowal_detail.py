"""
tests/test_sowal_detail.py
Tests for the SoWal detail crawler.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.crawlers.sowal_detail import SoWalDetailCrawler
from app.crawlers.policy import CrawlPolicy
from app.crawlers.registry import ALL_CRAWLERS


def test_sowal_detail_is_registered():
    """Verify the detail crawler is registered in the pipeline."""
    names = [c.name for c in ALL_CRAWLERS]
    assert "sowal_detail" in names


def test_sowal_detail_crawler_init_with_default_policy():
    """Detail crawler initializes with a default policy."""
    crawler = SoWalDetailCrawler()
    assert crawler.policy is not None
    assert crawler.policy.request_delay == 0.5


def test_sowal_detail_crawler_init_with_custom_policy():
    """Detail crawler accepts injected policy."""
    policy = CrawlPolicy(max_events=2, request_delay=0.1)
    crawler = SoWalDetailCrawler(policy=policy)
    assert crawler.policy.max_events == 2
    assert crawler.policy.request_delay == 0.1


def test_sowal_detail_crawler_has_urls():
    """Detail crawler has a list of URLs to crawl."""
    crawler = SoWalDetailCrawler()
    assert len(crawler.DETAIL_URLS) > 0
    assert all(url.startswith("https://sowal.com/event/") for url in crawler.DETAIL_URLS)


def test_sowal_detail_crawler_policy_limits_urls():
    """Policy limits the number of URLs fetched."""
    policy = CrawlPolicy(max_events=2)
    crawler = SoWalDetailCrawler(policy=policy)
    limited = policy.limit(crawler.DETAIL_URLS)
    assert len(limited) == 2


def test_sowal_detail_crawler_name():
    """Verify the crawler has the correct name."""
    crawler = SoWalDetailCrawler()
    assert crawler.name == "sowal_detail"
