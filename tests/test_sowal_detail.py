"""
tests/test_sowal_detail.py
Tests for the SoWal detail crawler.
"""
import sys
from pathlib import Path

from bs4 import BeautifulSoup

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


def test_sowal_detail_crawler_has_title_patterns():
    """Detail crawler tracks a list of recurring titles, not fixed URLs."""
    crawler = SoWalDetailCrawler()
    assert len(crawler.TITLE_PATTERNS) > 0
    assert all(isinstance(p, str) and p for p in crawler.TITLE_PATTERNS)


def test_sowal_detail_crawler_name():
    """Verify the crawler has the correct name."""
    crawler = SoWalDetailCrawler()
    assert crawler.name == "sowal_detail"


# --- URL discovery (no network; requests.get is monkeypatched) -------------

_LISTING_HTML = """
<html><body>
  <table class="views-table">
    <caption>Wednesday, July 15, 2026</caption>
    <tr><td>9:30 am</td><td><a href="/event/live-music-crackings-541">Live Music @ Crackings</a></td></tr>
    <tr><td>6:00 pm</td><td><a href="/event/30avenue-summer-concert-series-42">30Avenue Summer Concert Series</a></td></tr>
  </table>
  <table class="views-table">
    <caption>Thursday, July 16, 2026</caption>
    <tr><td>9:30 am</td><td><a href="/event/live-music-crackings-542">Live Music @ Crackings</a></td></tr>
    <tr><td>6:00 pm</td><td><a href="/event/30avenue-summer-concert-series-43">30Avenue Summer Concert Series</a></td></tr>
    <tr><td>6:00 pm</td><td><a href="/event/harbor-nights-at-harborwalk-1">Harbor Nights at HarborWalk</a></td></tr>
  </table>
</body></html>
"""


class _FakeListingResponse:
    def __init__(self, text):
        self.text = text
        self.status_code = 200

    def raise_for_status(self):
        pass


def test_discover_urls_picks_earliest_matching_date(monkeypatch):
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeListingResponse(_LISTING_HTML))

    crawler = SoWalDetailCrawler()
    crawler.TITLE_PATTERNS = ["Live Music @ Crackings", "30Avenue Summer Concert Series"]
    urls = crawler._discover_urls()

    assert "https://sowal.com/event/live-music-crackings-541" in urls
    assert "https://sowal.com/event/live-music-crackings-542" not in urls
    assert "https://sowal.com/event/30avenue-summer-concert-series-42" in urls


def test_discover_urls_matches_case_and_whitespace_insensitively(monkeypatch):
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeListingResponse(_LISTING_HTML))

    crawler = SoWalDetailCrawler()
    crawler.TITLE_PATTERNS = ["  live music @ crackings  "]
    urls = crawler._discover_urls()

    assert urls == ["https://sowal.com/event/live-music-crackings-541"]


def test_discover_urls_skips_pattern_with_no_current_match(monkeypatch, caplog):
    import logging
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeListingResponse(_LISTING_HTML))

    crawler = SoWalDetailCrawler()
    crawler.TITLE_PATTERNS = ["Live Music @ Crackings", "Some Retired Series Nobody Runs Anymore"]
    with caplog.at_level(logging.WARNING):
        urls = crawler._discover_urls()

    assert urls == ["https://sowal.com/event/live-music-crackings-541"]
    assert "Some Retired Series Nobody Runs Anymore" in caplog.text


def test_discover_urls_empty_when_listing_fetch_fails(monkeypatch):
    import requests

    def raise_error(*a, **k):
        raise requests.exceptions.ConnectionError("boom")

    monkeypatch.setattr(requests, "get", raise_error)

    crawler = SoWalDetailCrawler()
    assert crawler._discover_urls() == []
