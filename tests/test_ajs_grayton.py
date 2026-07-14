"""
tests/test_ajs_grayton.py
Tests for the AJ's Grayton Beach crawler's pure HTML-parsing helpers, its
registration, and that its output normalises correctly. No network calls.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.crawlers.ajs_grayton import AJsGraytonCrawler, _extract_performer, _split_datetime, parse_events
from app.crawlers.registry import ALL_CRAWLERS
from app.normalize import normalize_events


def _section(event_id: str, title: str, start: str, end: str, description: str = "") -> str:
    return f"""
    <section id="{event_id}"><div class="row event-content">
    <div class="event-text-holder"><h2>{title}</h2>
    <div class="event-add-to-calendar"><span class="addtocalendar atc-style-blue">
    <var class="atc_event">
    <var class="atc_date_start">{start}</var>
    <var class="atc_date_end">{end}</var>
    <var class="atc_timezone">America/Chicago</var>
    <var class="atc_title">{title}</var>
    <var class="atc_description">{description}</var>
    <var class="atc_location">AJ's Grayton Beach</var>
    </var></span></div></div></div></section>
    """


SAMPLE_HTML = "<html><body>" + "".join([
    _section("100", "Live Music with Kevin Carson at AJ's Grayton Beach",
              "2026-07-20 17:00:00", "2026-07-20 21:00:00"),
    _section("101", "Live Music: Dion Jones &amp; The Neon Tears",
              "2026-07-15 18:00:00", "2026-07-15 21:00:00"),
    _section("102", "Jim Couch - Thursday Nights",
              "2026-07-16 16:00:00", "2026-07-16 20:00:00"),
    _section("103", "3HG", "2026-07-24 21:00:00", "2026-07-25 01:00:00"),
    _section("104", "Karaoke", "2026-07-16 21:00:00", "2026-07-17 00:00:00"),
    _section("105", "Teachers Back-to-School Happy Hour at AJ's",
             "2026-08-07 16:00:00", "2026-08-07 18:00:00"),
]) + "</body></html>"


# --- pure parsing helpers ----------------------------------------------------

def test_split_datetime_converts_to_date_and_12h_time():
    assert _split_datetime("2026-07-20 17:00:00") == ("2026-07-20", "5:00 pm")
    assert _split_datetime("2026-07-20 09:30:00") == ("2026-07-20", "9:30 am")
    assert _split_datetime("2026-07-20 00:00:00") == ("2026-07-20", "12:00 am")
    assert _split_datetime("2026-07-20 12:00:00") == ("2026-07-20", "12:00 pm")


def test_split_datetime_rejects_garbage():
    assert _split_datetime("") == (None, None)
    assert _split_datetime("not a date") == (None, None)


def test_extract_performer_with_pattern_stops_before_venue_suffix():
    assert _extract_performer("Live Music with Kevin Carson at AJ's Grayton Beach") == "Kevin Carson"


def test_extract_performer_colon_pattern_keeps_full_band_name():
    # regression: the with-pattern's name cap would truncate this to
    # "Dion Jones & The Neon" (5-token limit) -- the colon split avoids it
    assert _extract_performer("Live Music: Dion Jones & The Neon Tears") == "Dion Jones & The Neon Tears"


def test_extract_performer_strips_trailing_dash_qualifier():
    assert _extract_performer("Jim Couch - Thursday Nights") == "Jim Couch"


def test_extract_performer_bare_title_with_leading_digit():
    assert _extract_performer("3HG") == "3HG"


def test_extract_performer_skips_karaoke_category():
    assert _extract_performer("Karaoke") is None


def test_extract_performer_skips_non_music_promo():
    assert _extract_performer("Teachers Back-to-School Happy Hour at AJ's") is None
    assert _extract_performer("AJ's Tin Cup Classic Golf Tournament") is None
    assert _extract_performer("Contractors Connect Meet Up") is None


def test_extract_performer_none_for_blank_title():
    assert _extract_performer("") is None
    assert _extract_performer(None) is None


# --- page parsing -------------------------------------------------------------

def test_parse_events_extracts_only_the_four_music_events():
    events = parse_events(SAMPLE_HTML)
    performers = {e["performer"] for e in events}
    assert performers == {"Kevin Carson", "Dion Jones & The Neon Tears", "Jim Couch", "3HG"}
    assert len(events) == 4   # Karaoke and the happy hour are excluded


def test_parse_events_sets_date_time_venue_and_source():
    events = parse_events(SAMPLE_HTML)
    kevin = next(e for e in events if e["performer"] == "Kevin Carson")
    assert kevin["date"] == "2026-07-20"
    assert kevin["time_start"] == "5:00 pm"
    assert kevin["time_end"] == "9:00 pm"
    assert kevin["venue"] == "AJ's Grayton Beach"
    assert kevin["source"] == "ajs_grayton"
    assert kevin["name"] == "Kevin Carson at AJ's Grayton Beach"


def test_parse_events_url_includes_section_anchor():
    events = parse_events(SAMPLE_HTML)
    kevin = next(e for e in events if e["performer"] == "Kevin Carson")
    assert kevin["url"].endswith("#100")


def test_parse_events_empty_page_returns_no_events():
    assert parse_events("<html><body></body></html>") == []


# --- registration + normalisation --------------------------------------------

def test_ajs_grayton_registered():
    names = [c.name for c in ALL_CRAWLERS]
    assert "ajs_grayton" in names


def test_ajs_grayton_output_normalises_cleanly():
    normalised = normalize_events(parse_events(SAMPLE_HTML))
    assert len(normalised) == 4
    assert all(e.get("venue") == "AJ's Grayton Beach" for e in normalised)


def test_crawler_survives_network_failure(monkeypatch):
    def boom(*a, **kw):
        raise ConnectionError("nope")

    import app.crawlers.ajs_grayton as mod
    monkeypatch.setattr(mod.requests, "get", boom)
    assert AJsGraytonCrawler().fetch() == []
