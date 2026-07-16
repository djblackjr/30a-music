"""
tests/test_sowal_more_events_widget.py
Tests for the "More Events at X" widget parser
(app/crawlers/sowal.py::SoWalCrawler._parse_more_events_widget).

Event pages often carry this widget below the main content: one
<table class="views-table"> per date, each with a <caption> date and rows
linking to that date's own event node -- the exact same shape as the main
listing's calendar tables. It was initially mistaken for duplicate junk
(_in_calendar_widget) because the per-table <caption> dates were never being
read; this is real, useful, otherwise-unreachable data (confirmed live on
Old Florida Fish House's page: distinct "Dueling Pianos" / "Jake & Aimee"
slots the crawler had no other way to see).

No network. HTML fixture mirrors the real page structure (verified via
BeautifulSoup dump of the live pages).
"""
import sys
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.crawlers.sowal import SoWalCrawler

_WIDGET_HTML = """
<html><body>
  <h1>Live Music @ Old Florida Fish House</h1>
  <div class="view view-date-calendar-lists view-id-date_calendar_lists view-display-id-panel_pane_11">
    <div class="view-header"><span class="section-header-text">More Events at Old Florida Fish House</span></div>
    <div class="view-content">
      <table class="views-table cols-0">
        <caption><span class="date-display-single">July 16, 2026</span></caption>
        <tbody>
          <tr><td><span class="date-display-single">5:00 pm</span></td>
              <td class="priority-medium"><a href="/event/live-music-old-florida-fish-house-127">Live Music @ Old Florida Fish House</a></td>
              <td>Old Florida Fish House</td></tr>
          <tr><td><span class="date-display-single">7:00 pm</span></td>
              <td class="priority-medium"><a href="/event/dueling-pianos-old-florida-fish-house-330">Dueling Pianos @ Old Florida Fish House</a></td>
              <td>Old Florida Fish House</td></tr>
        </tbody>
      </table>
      <table class="views-table cols-0">
        <caption><span class="date-display-single">July 19, 2026</span></caption>
        <tbody>
          <tr><td><span class="date-display-single">7:00 pm</span></td>
              <td class="priority-medium"><a href="/event/jake-aimee-old-florida-fish-house-15">Jake &amp; Aimee @ Old Florida Fish House</a></td>
              <td>Old Florida Fish House</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</body></html>
"""

_REPEATED_SERIES_WIDGET_HTML = """
<html><body>
  <h1>30Avenue Summer Concert Series</h1>
  <div class="view view-date-calendar-lists view-id-date_calendar_lists view-display-id-panel_pane_11">
    <table class="views-table cols-0">
      <caption><span class="date-display-single">July 16, 2026</span></caption>
      <tbody><tr><td>6:00 pm</td><td><a href="/event/30avenue-summer-concert-series-43">30Avenue Summer Concert Series</a></td><td>30Avenue</td></tr></tbody>
    </table>
    <table class="views-table cols-0">
      <caption><span class="date-display-single">July 17, 2026</span></caption>
      <tbody><tr><td>6:00 pm</td><td><a href="/event/30avenue-summer-concert-series-44">30Avenue Summer Concert Series</a></td><td>30Avenue</td></tr></tbody>
    </table>
  </div>
</body></html>
"""


def test_more_events_widget_includes_real_named_and_generic_rows():
    soup = BeautifulSoup(_WIDGET_HTML, "lxml")
    obs = SoWalCrawler()._parse_more_events_widget(soup, own_date="2026-07-15", default_venue="Old Florida Fish House")

    by_date = {(o["date"], o["performer"]) for o in obs}
    assert ("2026-07-16", "Dueling Pianos") in by_date
    assert ("2026-07-19", "Jake & Aimee") in by_date
    # "Live Music @ ..." stays honestly unresolved -- no fake performer.
    unresolved = [o for o in obs if o["date"] == "2026-07-16" and o["performer"] is None]
    assert len(unresolved) == 1
    assert unresolved[0]["performer_status"] == "unresolved"


def test_more_events_widget_skips_own_date():
    soup = BeautifulSoup(_WIDGET_HTML, "lxml")
    obs = SoWalCrawler()._parse_more_events_widget(soup, own_date="2026-07-16", default_venue="Old Florida Fish House")
    assert all(o["date"] != "2026-07-16" for o in obs)


def test_more_events_widget_skips_repeated_series_title_with_no_venue_split():
    # "30Avenue Summer Concert Series" has no ' @ Venue' split and repeats
    # verbatim across every row -- with no per-row description to recover a
    # real name from, don't invent a fake performer for each date.
    soup = BeautifulSoup(_REPEATED_SERIES_WIDGET_HTML, "lxml")
    obs = SoWalCrawler()._parse_more_events_widget(soup, own_date="2026-07-15", default_venue="30Avenue")
    assert obs == []


def test_more_events_widget_empty_when_absent():
    soup = BeautifulSoup("<html><body><h1>X</h1></body></html>", "lxml")
    assert SoWalCrawler()._parse_more_events_widget(soup, own_date=None, default_venue=None) == []
