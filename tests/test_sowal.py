"""
tests/test_sowal.py
Tests for the SoWal crawler's pure parsing helpers, its registration, that its
output normalises correctly, and that it reconciles under the identity model.
No network calls.
"""
import sys
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.crawlers.sowal import SoWalCrawler, parse_time, parse_when, split_title
from app.crawlers.policy import CrawlPolicy
from app.crawlers.registry import ALL_CRAWLERS
from app.normalize import normalize_events
from app.reconcile.changes import compare_runs


# --- crawl policy (strategy separated from implementation) -----------------

def test_crawl_policy_defaults():
    p = CrawlPolicy()
    assert p.max_events is None
    assert p.max_pages is None
    assert p.request_delay == 1.0


def test_crawl_policy_limit_unbounded_by_default():
    assert CrawlPolicy().limit([1, 2, 3, 4, 5]) == [1, 2, 3, 4, 5]


def test_crawl_policy_limit_caps_events():
    assert CrawlPolicy(max_events=2).limit([1, 2, 3, 4, 5]) == [1, 2]


def test_sowal_uses_default_policy_when_none():
    assert SoWalCrawler().policy.max_events is None


def test_sowal_accepts_injected_policy():
    c = SoWalCrawler(policy=CrawlPolicy(max_events=3, request_delay=0))
    assert c.policy.max_events == 3
    assert c.policy.request_delay == 0


# --- parsing helpers -------------------------------------------------------

def test_split_title_performer_and_venue():
    assert split_title("Duncan Crittenden @ Local Catch Bar & Grill") == (
        "Duncan Crittenden", "Local Catch Bar & Grill")


def test_split_title_no_venue():
    assert split_title("Open Mic Night") == ("Open Mic Night", None)


def test_split_title_falls_back_to_trailing_at():
    # No '@' on this page's title -- verified live it has no 'Where:' label
    # either, so this trailing "... at Venue" is the only venue signal at all.
    assert split_title("Here Comes the Sun Summer Concert Series at Rosemary Beach") == (
        "Here Comes the Sun Summer Concert Series", "Rosemary Beach")


def test_split_title_at_split_uses_last_occurrence():
    assert split_title("Live at Leeds at The Bay") == ("Live at Leeds", "The Bay")


def test_split_title_at_split_rejects_a_showtime():
    # "Trivia at 7pm" should not be misread as a venue named "7pm".
    assert split_title("Trivia at 7pm") == ("Trivia at 7pm", None)
    assert split_title("Trivia at 7:30 PM") == ("Trivia at 7:30 PM", None)


def test_parse_when_full_format():
    assert parse_when("Saturday, July 11, 2026") == "2026-07-11"


def test_parse_when_unparseable():
    assert parse_when("this weekend") is None
    assert parse_when(None) is None


def test_parse_time_range():
    text = "When:\nSaturday, July 11, 2026\nTime:\n5:00 pm\nto\n8:00 pm\nWhere:\nLocal Catch"
    assert parse_time(text) == ("5:00 pm", "8:00 pm")


def test_parse_time_single():
    text = "Time:\n7:00 pm\nWhere:\nSomewhere"
    assert parse_time(text) == ("7:00 pm", None)


# --- calendar-row parsing ----------------------------------------------------
# Regression coverage for a real bug: a malformed row on a live "More Events"
# widget had its <a> wrap a whole trailing paragraph instead of just its
# title, producing a 500+ character "title" that was literally the venue's
# full body description and got saved to the DB as a performer name.

def test_parse_calendar_rows_skips_malformed_oversized_title():
    huge_title = "Six Piece Suits " + ("Come out to HarborWalk Village along the Destin Harbor. " * 10)
    html = f"""
    <table class="views-table">
      <caption>July 23, 2026</caption>
      <tr><td>6:00 pm</td><td><a href="/event/malformed">{huge_title}</a></td><td>Venue</td></tr>
      <tr><td>7:00 pm</td><td><a href="/event/fine">A Real Title</a></td><td>Venue</td></tr>
    </table>
    """
    soup = BeautifulSoup(html, "lxml")
    rows = SoWalCrawler()._parse_calendar_rows(soup)
    assert len(rows) == 1
    assert rows[0]["title"] == "A Real Title"


# --- registration ----------------------------------------------------------

def test_sowal_is_registered():
    names = [c.name for c in ALL_CRAWLERS]
    assert "sowal" in names


def test_sowal_registered_with_production_policy():
    sowal = next(c for c in ALL_CRAWLERS if c.name == "sowal")
    assert sowal.policy.max_events == 150
    assert sowal.policy.request_delay == 0.75


# --- normalisation of SoWal output -----------------------------------------

def _sowal_event(performer, venue, date, time_start="5:00 pm"):
    return {
        "name": f"{performer} @ {venue}",
        "performer": performer,
        "venue": venue,
        "date": date,
        "time_start": time_start,
        "time_end": None,
        "url": "https://sowal.com/event/x",
        "stage": None,
        "source": "sowal",
    }


def test_sowal_output_normalises_and_scores():
    out = normalize_events([_sowal_event("Duncan Crittenden", "Local Catch Bar & Grill", "2026-07-11")])
    ev = out[0]
    assert ev["source"] == "sowal"
    assert 0.0 <= ev["confidence"] <= 1.0
    # sowal base trust 0.9, all fields present -> 0.9
    assert ev["confidence"] == 0.9


# --- reconciliation under the identity model --------------------------------

def test_sowal_reconciliation_stable_across_identical_runs():
    run = normalize_events([_sowal_event("A", "V", "2026-07-11")])
    result = compare_runs(run, run)
    assert result["summary"]["unchanged"] == 1
    assert result["summary"]["total_delta"] == 0


def test_sowal_time_change_is_changed():
    prev = normalize_events([_sowal_event("A", "V", "2026-07-11", time_start="5:00 pm")])
    curr = normalize_events([_sowal_event("A", "V", "2026-07-11", time_start="7:00 pm")])
    result = compare_runs(curr, prev)
    assert result["summary"]["changed"] == 1
    assert result["summary"]["new"] == 0
    assert result["summary"]["removed"] == 0


# --- full fetch(): enrichment still fires for a known recurring series -----
# Regression: split_title's ' at Venue' fallback resolves the venue half of
# a title like "Here Comes the Sun ... at Rosemary Beach" from the bare
# title alone -- but needs_enrichment used to treat a non-None venue as
# "already resolved enough" and skip the enrichment fetch entirely, so the
# performer half (still just the "whole title is the performer" catch-all
# guess) was saved straight to the DB with no chance for resolve_performer's
# RECURRING_SERIES_TITLES protection to ever run. Confirmed live: this
# produced a real duplicate on the dashboard (a stale "Scratch 2020,
# venue=None" row next to the correct "Scratch 2020, venue=Rosemary Beach").

_LISTING_HTML_ROSEMARY = """
<html><body>
  <table class="views-table">
    <caption>Wednesday, July 15, 2026</caption>
    <tr><td>7:00 pm</td>
        <td><a href="/event/here-comes-the-sun-summer-concert-series-at-rosemary-beach-4">Here Comes the Sun Summer Concert Series at Rosemary Beach</a></td></tr>
  </table>
</body></html>
"""

_ENRICHMENT_HTML_ROSEMARY = """
<html><body>
  <h1>Here Comes the Sun Summer Concert Series at Rosemary Beach</h1>
  <p>When: Wednesday, July 15, 2026</p>
  <p>Time: 7:00 pm to 8:30 pm</p>
  <p>Wednesdays in Rosemary Beach mean music. June 3: Sons of Saints July 15: Scratch 2020 July 22: Killer Robot Army</p>
</body></html>
"""


class _FakeResp:
    def __init__(self, text):
        self.text = text
        self.status_code = 200

    def raise_for_status(self):
        pass


def test_fetch_still_enriches_known_series_with_at_split_venue(monkeypatch):
    import requests

    def fake_get(url, headers=None, timeout=None):
        if "events" in url and "event/" not in url:
            return _FakeResp(_LISTING_HTML_ROSEMARY)
        return _FakeResp(_ENRICHMENT_HTML_ROSEMARY)

    monkeypatch.setattr(requests, "get", fake_get)

    events = SoWalCrawler(policy=CrawlPolicy(request_delay=0)).fetch()
    perfs = {e["performer"] for e in events}

    # The real, prose-lineup-resolved act -- not the fake series-title guess.
    assert "Scratch 2020" in perfs
    assert "Here Comes the Sun Summer Concert Series" not in perfs
