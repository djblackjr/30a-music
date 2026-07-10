"""
app/crawlers/policy.py
Crawl strategy, separated from crawl implementation.

A CrawlPolicy describes HOW politely/exhaustively to crawl — not how to parse a
site. Crawlers accept a policy and honour it. The default policy is exhaustive
and polite: no caps, one second between requests. Development or test runs pass
an explicit policy (e.g. CrawlPolicy(max_events=5, request_delay=0)) instead of
the crawler carrying its own knobs.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class CrawlPolicy:
    max_events: Optional[int] = None    # cap on events collected per run; None = unbounded
    max_pages: Optional[int] = None     # cap on listing pages followed; None = unbounded
    request_delay: float = 1.0          # seconds between HTTP requests (politeness)

    def limit(self, items) -> list:
        """Apply max_events to a sequence, returning a (possibly) truncated list."""
        items = list(items)
        if self.max_events is None:
            return items
        return items[: self.max_events]
