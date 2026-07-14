"""
tests/test_shunk_gulley.py
Tests for the Shunk Gulley crawler's pure ICS-parsing helpers, its
registration, and that its output normalises correctly. No network calls.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.crawlers.registry import ALL_CRAWLERS
from app.crawlers.shunk_gulley import ShunkGulleyCrawler, _unescape, _unfold, parse_ics
from app.normalize import normalize_events

SAMPLE_ICS = """BEGIN:VCALENDAR
METHOD:PUBLISH
CALSCALE:GREGORIAN
PRODID:-//tockify.com/1
VERSION:2.0
X-WR-CALNAME:Shunk Music
X-WR-TIMEZONE:America/Chicago
BEGIN:VEVENT
UID:TKF/abc/1
DTSTART:20260613T220000Z
DTEND:20260614T020000Z
URL:https://tockify.com/shunk/detail/338/1781388000000
SUMMARY: Justin Fobes
DESCRIPTION:Line one\\, with a comma\\nLine two continued on
 a folded line
CATEGORIES:
END:VEVENT
BEGIN:VEVENT
UID:TKF/abc/2
DTSTART:20260618T220000Z
DTEND:20260619T020000Z
URL:https://tockify.com/shunk/detail/339/1781388000000
SUMMARY: Ronnie Presley
END:VEVENT
END:VCALENDAR
"""


# --- pure parsing helpers ----------------------------------------------------

def test_unfold_joins_continuation_lines():
    # RFC 5545: the single fold whitespace character is removed, not kept as
    # a literal space -- a continuation line's own content starts right where
    # its leading space/tab was.
    text = "SUMMARY:Foo\n DESCRIPTION:bar\n  continued\nEND:VEVENT"
    assert _unfold(text) == ["SUMMARY:FooDESCRIPTION:bar continued", "END:VEVENT"]


def test_unescape_handles_commas_semicolons_and_newlines():
    assert _unescape("a\\, b\\; c\\nd") == "a, b; c\nd"


def test_parse_ics_extracts_both_events():
    events = parse_ics(SAMPLE_ICS)
    assert len(events) == 2
    assert {e["performer"] for e in events} == {"Justin Fobes", "Ronnie Presley"}


def test_parse_ics_converts_utc_to_local_time_and_date():
    events = parse_ics(SAMPLE_ICS)
    justin = next(e for e in events if e["performer"] == "Justin Fobes")
    # 20260613T220000Z -> 5:00 pm America/Chicago (CDT, UTC-5), same calendar date
    assert justin["date"] == "2026-06-13"
    assert justin["time_start"] == "5:00 pm"
    assert justin["time_end"] == "9:00 pm"


def test_parse_ics_unfolds_and_unescapes_description():
    justin = next(e for e in parse_ics(SAMPLE_ICS) if e["performer"] == "Justin Fobes")
    # description isn't carried into the event dict (not part of the shared
    # crawler output shape), but folding/unescaping must not corrupt SUMMARY
    # or any other field on a VEVENT that also has a folded DESCRIPTION
    assert justin["performer"] == "Justin Fobes"


def test_parse_ics_sets_venue_and_source():
    for e in parse_ics(SAMPLE_ICS):
        assert e["venue"] == "Shunk Gulley"
        assert e["source"] == "shunk_gulley"
        assert e["name"] == f"{e['performer']} at Shunk Gulley"


def test_parse_ics_skips_events_without_a_parsable_start():
    broken = SAMPLE_ICS.replace("DTSTART:20260613T220000Z", "DTSTART;VALUE=DATE:20260613")
    events = parse_ics(broken)
    assert len(events) == 1
    assert events[0]["performer"] == "Ronnie Presley"


def test_parse_ics_empty_feed_returns_no_events():
    assert parse_ics("BEGIN:VCALENDAR\nEND:VCALENDAR\n") == []


# --- registration + normalisation --------------------------------------------

def test_shunk_gulley_registered():
    names = [c.name for c in ALL_CRAWLERS]
    assert "shunk_gulley" in names


def test_shunk_gulley_output_normalises_cleanly():
    events = parse_ics(SAMPLE_ICS)
    normalised = normalize_events(events)
    assert len(normalised) == 2
    assert all(e.get("venue") == "Shunk Gulley" for e in normalised)


def test_crawler_survives_network_failure(monkeypatch):
    def boom(*a, **kw):
        raise ConnectionError("nope")

    import app.crawlers.shunk_gulley as mod
    monkeypatch.setattr(mod.requests, "get", boom)
    assert ShunkGulleyCrawler().fetch() == []
