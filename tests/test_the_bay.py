"""
tests/test_the_bay.py
Tests for The Bay's pure JSON-parsing helpers, its registration, and that
its output normalises correctly. No network calls.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.crawlers.registry import ALL_CRAWLERS
from app.crawlers.the_bay import (
    TheBayCrawler,
    _extract_performer,
    _lineup_performer_for_date,
    _split_local_datetime,
    parse_events,
)
from app.normalize import normalize_events

BONFIRE_DESCRIPTION = (
    "<p>Get ready to cozy up by the&nbsp;bonfires Wednesdays&nbsp;from&nbsp;5-8 PM "
    "as we light up the night! 🌅🔥 2026 LINEUP: 🎶 June 10 - Martin Lane 🥁 June 17 - "
    "Arrowgrass ⁠ 🎤 June 24 - Jared Herzog ⁠ ✨ It&rsquo;s the ultimate way to unwind!</p>"
)


def _event(summary, start, end, description=""):
    return {
        "summary": summary,
        "timezoneStart": start,
        "timezoneEnd": end,
        "longDescription": description,
        "url": "https://thebay.eventcalendarapp.com/some-event",
    }


# --- pure parsing helpers ----------------------------------------------------

def test_split_local_datetime_no_tz_math_needed():
    assert _split_local_datetime("2026-07-19T16:00:00") == ("2026-07-19", "4:00 pm")
    assert _split_local_datetime("2026-07-15T17:00:00") == ("2026-07-15", "5:00 pm")
    assert _split_local_datetime("2026-07-15T00:00:00") == ("2026-07-15", "12:00 am")


def test_split_local_datetime_rejects_garbage():
    assert _split_local_datetime(None) == (None, None)
    assert _split_local_datetime("not a date") == (None, None)


def test_extract_performer_with_pattern_in_title():
    assert _extract_performer("Sunday Pickin' with Mike Whitty & Friends", "", "2026-07-19") \
        == "Mike Whitty & Friends"


def test_lineup_performer_for_date_matches_listed_date():
    assert _lineup_performer_for_date(BONFIRE_DESCRIPTION, "2026-06-17") == "Arrowgrass"
    assert _lineup_performer_for_date(BONFIRE_DESCRIPTION, "2026-06-10") == "Martin Lane"
    assert _lineup_performer_for_date(BONFIRE_DESCRIPTION, "2026-06-24") == "Jared Herzog"


def test_lineup_performer_for_date_none_when_date_not_listed():
    # site's lineup text hasn't been updated to cover this date yet --
    # must not guess or fall back to some other entry
    assert _lineup_performer_for_date(BONFIRE_DESCRIPTION, "2026-07-15") is None


def test_extract_performer_generic_title_routes_through_lineup():
    assert _extract_performer("Wednesday Night Bonfire & Live Music", BONFIRE_DESCRIPTION, "2026-06-17") \
        == "Arrowgrass"
    assert _extract_performer("Wednesday Night Bonfire & Live Music", BONFIRE_DESCRIPTION, "2026-07-15") \
        is None


def test_extract_performer_none_for_blank_summary():
    assert _extract_performer("", "", "2026-07-19") is None
    assert _extract_performer(None, "", "2026-07-19") is None


# --- page parsing -------------------------------------------------------------

def test_parse_events_skips_bonfire_without_lineup_match_keeps_named_act():
    payload = {"events": [
        _event("Wednesday Night Bonfire & Live Music", "2026-07-15T17:00:00", "2026-07-15T20:00:00",
               BONFIRE_DESCRIPTION),
        _event("Sunday Pickin' with Mike Whitty & Friends", "2026-07-19T16:00:00", "2026-07-19T20:00:00"),
    ]}
    events = parse_events(payload)
    assert len(events) == 1
    assert events[0]["performer"] == "Mike Whitty & Friends"


def test_parse_events_keeps_bonfire_when_lineup_covers_the_date():
    payload = {"events": [
        _event("Wednesday Night Bonfire & Live Music", "2026-06-17T17:00:00", "2026-06-17T20:00:00",
               BONFIRE_DESCRIPTION),
    ]}
    events = parse_events(payload)
    assert len(events) == 1
    assert events[0]["performer"] == "Arrowgrass"
    assert events[0]["date"] == "2026-06-17"
    assert events[0]["time_start"] == "5:00 pm"
    assert events[0]["time_end"] == "8:00 pm"


def test_parse_events_sets_venue_and_source():
    payload = {"events": [
        _event("Sunday Pickin' with Mike Whitty & Friends", "2026-07-19T16:00:00", "2026-07-19T20:00:00"),
    ]}
    [ev] = parse_events(payload)
    assert ev["venue"] == "The Bay"
    assert ev["source"] == "the_bay"
    assert ev["name"] == "Mike Whitty & Friends at The Bay"


def test_parse_events_skips_entries_missing_start_time():
    payload = {"events": [_event("Some Show", None, None)]}
    assert parse_events(payload) == []


def test_parse_events_empty_page_returns_no_events():
    assert parse_events({"events": []}) == []


# --- registration + normalisation --------------------------------------------

def test_the_bay_registered():
    names = [c.name for c in ALL_CRAWLERS]
    assert "the_bay" in names


def test_the_bay_output_normalises_cleanly():
    payload = {"events": [
        _event("Sunday Pickin' with Mike Whitty & Friends", "2026-07-19T16:00:00", "2026-07-19T20:00:00"),
    ]}
    normalised = normalize_events(parse_events(payload))
    assert len(normalised) == 1
    assert normalised[0]["venue"] == "The Bay"


def test_crawler_survives_network_failure(monkeypatch):
    def boom(*a, **kw):
        raise ConnectionError("nope")

    import app.crawlers.the_bay as mod
    monkeypatch.setattr(mod.requests, "get", boom)
    assert TheBayCrawler().fetch() == []


def test_crawler_follows_pagination_up_to_max_pages(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, page):
            self._page = page

        def raise_for_status(self):
            pass

        def json(self):
            next_page = f"https://api.eventcalendarapp.com/events?page={self._page + 1}" \
                if self._page < 20 else None
            return {
                "events": [_event(f"Band {self._page} with Someone", "2026-07-19T16:00:00", "2026-07-19T20:00:00")],
                "pages": {"nextPage": next_page},
            }

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        page = len(calls)
        return FakeResponse(page)

    import app.crawlers.the_bay as mod
    monkeypatch.setattr(mod.requests, "get", fake_get)
    events = TheBayCrawler().fetch()
    assert len(calls) == mod.MAX_PAGES   # stops at the cap, doesn't run away
    assert len(events) == mod.MAX_PAGES
