"""
tests/test_sowal_prose_lineup.py
Tests for the prose-lineup fix (app/crawlers/sowal.py::resolve_performer).

A recurring-series title with no ' @ Venue' split (e.g. "Baytowne Wednesday
Night Concert Series", "30Avenue Summer Concert Series") describes a PROGRAM,
not a single act, but is_generic_title can't tell -- the venue's own proper
noun breaks the "every token is generic" check, so classify_performer's
"whole title is the performer" catch-all invents a fake performer out of the
series name. resolve_performer() fixes this two ways:
  - an exact per-date match from an inline prose lineup ("July 15th: The
    Aces Band") always wins over the title guess
  - failing that, an explicit "see the full lineup below" pointer downgrades
    a title-guessed "named" result to unresolved, so a caller (e.g. the
    flyer-image fallback) gets a chance instead of a fabricated performer

No network. All fixtures below are real description text copied from the
live pages that exposed this bug.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.crawlers.sowal import (
    RECURRING_SERIES_TITLES,
    SoWalCrawler,
    classify_performer,
    parse_prose_lineup,
    resolve_performer,
)

# --- real page text fixtures -------------------------------------------------

_BAYTOWNE_DESC = (
    "Join The Village of Baytowne Wharf as they host the Wednesday Night "
    "Concert Series, a free weekly tradition on the Events Plaza Stage from "
    "7-9PM featuring local & regional talent from all kinds of genres. "
    "Bring your lawn chairs, blankets & your crew for some great tunes! "
    "July Concert Line Up "
    "July 1st: Below Alabama "
    "July 8th: TBA "
    "July 15th: The Aces Band "
    "July 22nd: Casey Kearney Band "
    "July 29th: Shenanigans"
)

_HERE_COMES_THE_SUN_DESC = (
    "Wednesdays in Rosemary Beach mean music, family and fun. Here Comes "
    "the Summer Summer Concert Series takes over the St. Augustine Green on "
    "Wednesdays, June 3 thru August 5, from 7PM - 8:30PM. "
    "June 3: Sons of Saints "
    "June 10: Boukou Groove "
    "June 17: Rock Mob "
    "June 24: Gage Cowart "
    "July 8: Davis & The Love "
    "July 15: Scratch 2020 "
    "July 22: Killer Robot Army "
    "July 29: Run Katie Run "
    "August 5: Will Thompson Band: Tribute to Tom Petter & Journey"
)

_HARBOR_NIGHTS_DESC = (
    "Harbor Nights are back in Destin. Every Thursday night, May 28th "
    "through July 30th, HarborWalk Village transforms into the ultimate "
    "summer evening experience from 6-9 PM. Summer 2026 Entertainment "
    "Lineup: "
    "May 28 : DJ Dance Party on the Lawn "
    "June 4 : Cade Pierce "
    "June 11 : DJ Dance Party on the Lawn "
    "June 18 : Live Band (Announced Soon) "
    "July 9 : Catalyst "
    "July 16 : DJ Dance Party on the Lawn "
    "July 23 : Six Piece Suits"
)

_30AVENUE_DESC = (
    "Come out to 30Avenue for the Summer Concert Series from 6PM til 9PM "
    "on the green. Guests are encouraged to bring a blanket or low-back "
    "lawn chairs. Gather your people for dinner, sips, shopping and LIVE "
    "MUSIC under the stars. See the full lineup below! 30Avenue is located "
    "at the intersection of Highway 98 and Scenic Highway 30A in Inlet "
    "Beach, Florida."
)


# --- classify_performer alone gets these wrong (documents the bug) ----------

def test_classify_performer_alone_invents_series_name_as_performer():
    c = classify_performer("Baytowne Wednesday Night Concert Series", _BAYTOWNE_DESC)
    assert c["performer"] == "Baytowne Wednesday Night Concert Series"
    assert c["performer_status"] == "named"
    assert c["extraction_method"] == "title"


# --- parse_prose_lineup ------------------------------------------------------

def test_parse_prose_lineup_baytowne():
    entries = parse_prose_lineup(_BAYTOWNE_DESC, 2026)
    assert entries["2026-07-15"] == "The Aces Band"
    assert entries["2026-07-01"] == "Below Alabama"
    assert entries["2026-07-08"] == "TBA"


def test_parse_prose_lineup_here_comes_the_sun():
    entries = parse_prose_lineup(_HERE_COMES_THE_SUN_DESC, 2026)
    assert entries["2026-07-15"] == "Scratch 2020"
    assert entries["2026-06-03"] == "Sons of Saints"


def test_parse_prose_lineup_harbor_nights():
    entries = parse_prose_lineup(_HARBOR_NIGHTS_DESC, 2026)
    assert entries["2026-07-16"] == "DJ Dance Party on the Lawn"
    assert entries["2026-06-04"] == "Cade Pierce"


def test_parse_prose_lineup_empty_without_year():
    assert parse_prose_lineup(_BAYTOWNE_DESC, None) == {}


# Regression coverage for a real bug: the LAST entry in a lineup has no
# following "Month Day:" marker to stop its non-greedy capture, so when the
# venue's promotional blurb immediately follows the lineup (the common
# case), the capture ran to the end of the description instead of stopping
# at the entry -- confirmed live: Baytowne's "July 29th: Shenanigans"
# swallowed the venue's entire trailing paragraph (800+ characters) straight
# into the DB as a performer name.
_BAYTOWNE_DESC_WITH_TRAILING_PROSE = _BAYTOWNE_DESC + (
    " The Village of Baytowne Wharf is the heart and soul of Sandestin Golf "
    "and Beach Resort. Featuring an array of boutiques, eateries, galleries "
    "and nightlife -- not to mention a jam-packed schedule of outdoor "
    "festivals and special events, it's easy to see why."
)


def test_parse_prose_lineup_drops_last_entry_swallowed_by_trailing_prose():
    entries = parse_prose_lineup(_BAYTOWNE_DESC_WITH_TRAILING_PROSE, 2026)
    # Earlier entries (bounded by a real next-entry marker) are unaffected.
    assert entries["2026-07-15"] == "The Aces Band"
    assert entries["2026-07-22"] == "Casey Kearney Band"
    # The last entry, with no next marker to bound it, is dropped rather
    # than trusted as an 800-character "performer name".
    assert "2026-07-29" not in entries


def test_parse_prose_lineup_empty_without_description():
    assert parse_prose_lineup(None, 2026) == {}
    assert parse_prose_lineup("no dates here", 2026) == {}


# --- resolve_performer: prose match wins over the title guess ---------------

def test_resolve_performer_uses_prose_match_over_title_guess():
    c = resolve_performer(
        "Baytowne Wednesday Night Concert Series", _BAYTOWNE_DESC, "2026-07-15", 2026
    )
    assert c["performer"] == "The Aces Band"
    assert c["performer_status"] == "named"
    assert c["extraction_method"] == "prose_lineup"


def test_resolve_performer_named_act_from_here_comes_the_sun():
    c = resolve_performer(
        "Here Comes the Sun Summer Concert Series at Rosemary Beach",
        _HERE_COMES_THE_SUN_DESC, "2026-07-15", 2026,
    )
    assert c["performer"] == "Scratch 2020"
    assert c["performer_status"] == "named"


def test_resolve_performer_dj_entry_is_category_not_fake_performer():
    c = resolve_performer(
        "Harbor Nights at HarborWalk", _HARBOR_NIGHTS_DESC, "2026-07-16", 2026
    )
    assert c["performer"] is None
    assert c["performer_status"] == "category"
    assert c["event_category"] == "dj"


def test_resolve_performer_tba_entry_is_unresolved_not_fake_performer():
    c = resolve_performer(
        "Baytowne Wednesday Night Concert Series", _BAYTOWNE_DESC, "2026-07-08", 2026
    )
    assert c["performer"] is None
    assert c["performer_status"] == "unresolved"


def test_resolve_performer_no_match_for_date_outside_lineup():
    # A date beyond what Baytowne's own July lineup blurb covers, e.g. reached
    # via the main crawler's enrichment for a September calendar row. No
    # "see the lineup" pointer phrase exists on this page at all -- but
    # Baytowne is a known ambiguous recurring series (RECURRING_SERIES_TITLES),
    # so the bare title is still never trusted as a performer without a
    # matching prose-lineup entry for THIS specific date.
    c = resolve_performer(
        "Baytowne Wednesday Night Concert Series", _BAYTOWNE_DESC, "2026-09-01", 2026
    )
    assert c["performer"] is None
    assert c["performer_status"] == "unresolved"


def test_baytowne_has_no_pointer_phrase_but_is_a_known_series():
    # Confirms *why* the test above needs RECURRING_SERIES_TITLES: the
    # pointer-phrase signal alone genuinely finds nothing on this page.
    from app.crawlers.sowal import _points_to_external_lineup
    assert "Baytowne Wednesday Night Concert Series" in RECURRING_SERIES_TITLES
    assert _points_to_external_lineup(_BAYTOWNE_DESC) is False


def test_resolve_performer_unaffected_for_titles_not_in_known_series():
    # A made-up ambiguous-looking title that ISN'T on the list keeps the
    # existing (pointer-phrase-only) behavior -- this fix is deliberately
    # scoped to verified cases, not a blanket "no @ means unresolved" rule.
    c = resolve_performer(
        "Some Other Weekly Thing", "No lineup or pointer phrase here at all.", "2026-09-01", 2026
    )
    assert c["extraction_method"] == "title"
    assert c["performer"] == "Some Other Weekly Thing"


# --- resolve_performer: no prose lineup, but an explicit pointer phrase -----

def test_resolve_performer_downgrades_title_guess_when_lineup_is_offpage():
    c = resolve_performer(
        "30Avenue Summer Concert Series", _30AVENUE_DESC, "2026-07-15", 2026
    )
    assert c["performer"] is None
    assert c["performer_status"] == "unresolved"
    assert c["extraction_method"] == "unresolved"


def test_resolve_performer_leaves_real_named_titles_alone():
    # A genuine "@ Venue" title must NOT be second-guessed by any of this.
    c = resolve_performer("Jordan Chase @ AJ's Grayton", "", "2026-07-15", 2026)
    assert c["performer"] == "Jordan Chase"
    assert c["performer_status"] == "named"
    assert c["extraction_method"] == "title"


# --- parse_event_observations: emit EVERY prose-lineup date, not just the --
# --- page's own -------------------------------------------------------------
# Regression: a prose lineup describing multiple dates (e.g. "Here Comes the
# Sun" covers 10 Wednesdays, June 3 - August 5) only ever produced ONE
# observation -- whichever date matched the page's own -- silently discarding
# every other date parse_prose_lineup had already correctly parsed. Confirmed
# live: this recovered July 22 ("Killer Robot Army") among others.

class _FakePageResponse:
    def __init__(self, text):
        self.text = text
        self.status_code = 200
        self.content = b""

    def raise_for_status(self):
        pass


_MULTI_DATE_PAGE_HTML = f"""
<html><body>
  <h1>Here Comes the Sun Summer Concert Series at Rosemary Beach</h1>
  <p>When: Wednesday, July 15, 2026</p>
  <p>Time: 7:00 pm to 8:30 pm</p>
  <p>{_HERE_COMES_THE_SUN_DESC}</p>
</body></html>
"""


def test_parse_event_observations_emits_every_prose_lineup_date(monkeypatch):
    import requests
    page_url = "https://sowal.com/event/here-comes-the-sun-summer-concert-series-at-rosemary-beach-4"
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakePageResponse(_MULTI_DATE_PAGE_HTML))

    obs = SoWalCrawler().parse_event_observations(page_url)
    by_date = {o["date"]: o for o in obs}

    # The page's own date, and others the page never directly "was".
    assert by_date["2026-07-15"]["performer"] == "Scratch 2020"
    assert by_date["2026-06-03"]["performer"] == "Sons of Saints"
    assert by_date["2026-07-22"]["performer"] == "Killer Robot Army"
    assert by_date["2026-07-29"]["performer"] == "Run Katie Run"

    # Every entry shares the page's one stated time and venue (from the
    # trailing " at Venue" title fallback -- no '@' or 'Where:' on this page).
    for o in obs:
        assert o["time_start"] == "7:00 pm"
        assert o["time_end"] == "8:30 pm"
        assert o["venue"] == "Rosemary Beach"
