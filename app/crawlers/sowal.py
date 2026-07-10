"""
app/crawlers/sowal.py
SoWal events crawler.

Crawls https://sowal.com/events, follows each event page, and extracts a
normalised event dict (performer, venue, ISO date, start/end time).

Event pages present fields as:
    <h1>Duncan Crittenden @ Local Catch Bar & Grill</h1>
    When:  Saturday, July 11, 2026
    Time:  5:00 pm  to  8:00 pm
    Where: Local Catch Bar & Grill

Duck-typed to the crawler protocol (has `name` and `fetch()`); registered in
app/crawlers/registry.py. Emitted events flow through app/normalize before save.
"""
import logging
import re
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from app.crawlers.policy import CrawlPolicy

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure parsing helpers (unit-tested without network)
# ---------------------------------------------------------------------------

# Date formats seen on SoWal event pages, most specific first.
_WHEN_FORMATS = ["%A, %B %d, %Y", "%B %d, %Y", "%A %B %d, %Y", "%m/%d/%Y", "%Y-%m-%d"]

_TIME_RANGE_RE  = re.compile(r"(\d{1,2}:\d{2}\s*[ap]m)\s*to\s*(\d{1,2}:\d{2}\s*[ap]m)", re.I)
_TIME_LABEL_RE  = re.compile(r"Time:\s*(.+)")


def split_title(title: str | None) -> tuple[str, str | None]:
    """'Performer @ Venue' -> ('Performer', 'Venue'); no ' @ ' -> (title, None)."""
    if not title:
        return "", None
    if " @ " in title:
        performer, venue = title.split(" @ ", 1)
        return performer.strip(), venue.strip() or None
    return title.strip(), None


def parse_when(raw: str | None) -> str | None:
    """Parse a SoWal 'When:' string into an ISO date, or None if unparseable."""
    if not raw:
        return None
    s = raw.strip()
    for fmt in _WHEN_FORMATS:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_time(text: str) -> tuple[str | None, str | None]:
    """Return (start, end). Prefers an explicit 'X to Y' range, else a single time."""
    m = _TIME_RANGE_RE.search(text)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    m = _TIME_LABEL_RE.search(text)
    if m:
        return m.group(1).split("\n")[0].strip(), None
    return None, None


# ---------------------------------------------------------------------------
# Crawler
# ---------------------------------------------------------------------------

class SoWalCrawler:
    name = "sowal"

    START_URL = "https://sowal.com/events"

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137 Safari/537.36"
        )
    }

    def __init__(self, policy: CrawlPolicy | None = None):
        # Strategy is injected; default is exhaustive and polite (see CrawlPolicy).
        self.policy = policy or CrawlPolicy()

    def fetch(self) -> list[dict]:
        logger.info("[SoWalCrawler] Crawling %s", self.START_URL)

        try:
            response = requests.get(self.START_URL, headers=self.HEADERS, timeout=20)
            response.raise_for_status()
        except Exception as exc:
            logger.warning("[SoWalCrawler] %s", exc)
            return []

        soup = BeautifulSoup(response.text, "lxml")

        event_links: list[str] = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/event/" in href:
                if href.startswith("/"):
                    href = "https://sowal.com" + href
                if href not in event_links:
                    event_links.append(href)

        total = len(event_links)
        event_links = self.policy.limit(event_links)
        logger.info(
            "[SoWalCrawler] Found %d event pages; crawling %d per policy",
            total, len(event_links),
        )

        events = []
        for i, url in enumerate(event_links):
            if i and self.policy.request_delay:
                time.sleep(self.policy.request_delay)
            event = self.parse_event(url)
            if event:
                events.append(event)

        logger.info("[SoWalCrawler] Parsed %d events", len(events))
        return events

    def parse_event(self, url: str) -> dict | None:
        try:
            response = requests.get(url, headers=self.HEADERS, timeout=20)
            response.raise_for_status()
        except Exception:
            return None

        soup = BeautifulSoup(response.text, "lxml")
        text = soup.get_text("\n", strip=True)

        h1 = soup.find("h1")
        title = h1.get_text(strip=True) if h1 else ""
        performer, venue_from_title = split_title(title)
        if not performer:
            return None

        # 'Where:' wins over the venue parsed from the title, when present.
        venue = venue_from_title
        m = re.search(r"Where:\s*(.+)", text)
        if m:
            venue = m.group(1).split("\n")[0].strip() or venue

        when = None
        m = re.search(r"When:\s*(.+)", text)
        if m:
            when = m.group(1).split("\n")[0].strip()
        date = parse_when(when)

        time_start, time_end = parse_time(text)

        return {
            "name":       title or performer,
            "performer":  performer,
            "venue":      venue,
            "date":       date,
            "time_start": time_start,
            "time_end":   time_end,
            "url":        url,
            "stage":      None,
            "source":     "sowal",
        }
