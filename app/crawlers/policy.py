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


# ---------------------------------------------------------------------------
# TODO (future): scheduler-selected crawl policies — NOT IMPLEMENTED YET
#
# The policy should be chosen by the SCHEDULER/entry point based on run context,
# not by the crawler. Planned named presets:
#
#     Policy        Purpose                     Example
#     -----------   -------------------------   ---------------------------
#     Development   Fast local testing          CrawlPolicy(max_events=5,   request_delay=0)
#     Production    Routine intraday runs        CrawlPolicy(max_events=100, request_delay=0.75)
#     Deep Scan     Scheduled reconciliation     CrawlPolicy(max_events=None, request_delay=1.0)
#
# Intended schedule mapping (to be wired when the scheduler exists):
#     10 AM / 3 PM / 8 PM reports  -> Production   (fresh intraday data)
#     Nightly 02:00                -> Deep Scan    (discover older/newly-added events)
#     Local development            -> Development
#
# Only the Production policy is wired today (in registry.py). Do not implement
# the scheduler here — this note records the target so the crawler never has to
# change to gain it.
#
# TODO (further out): make the crawler INCREMENTAL instead of full-site each run:
#   1. crawl the listing pages,
#   2. detect new or modified event links,
#   3. only fetch event pages that are unseen, changed, or not verified recently.
# This cuts requests dramatically while keeping coverage complete.
# ---------------------------------------------------------------------------
