"""
tests/test_sowal_extraction.py
Phase 3 — pure, offline tests for the SoWal extraction enrichment:
  - generic-title detection (narrow) and category detection
  - conservative performer-from-description extraction
  - multi-observation (lineup) parsing
  - named / unresolved / category partitioning
  - lower extraction confidence for description/lineup-derived performers

No network. Lineup tests build BeautifulSoup from inline HTML.
"""
import sys
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.crawlers.sowal import (
    SoWalCrawler,
    classify_performer,
    detect_category,
    detect_non_music,
    extract_performer_from_description,
    is_generic_title,
    parse_lineup_date,
    partition_observations,
)
from app.normalize.confidence import extraction_confidence


# --- generic-title detection (narrow) --------------------------------------

def test_generic_titles_detected():
    for t in ["Live Music", "Brunch & Live Music", "Music & Bonfire",
              "Summer Concert Series", "Live Band", "Live Entertainment"]:
        assert is_generic_title(t) is True, t


def test_named_titles_not_generic():
    for t in ["Duncan Crittenden", "Casey Kearney", "Boukou Groove",
              "Dion Jones & The Neon Tears"]:
        assert is_generic_title(t) is False, t


def test_category_labels_are_not_generic_live_music():
    # DJ/karaoke/trivia/open mic must NOT be classified as generic live music.
    for t in ["DJ Night", "Karaoke", "Trivia Night", "Open Mic"]:
        assert is_generic_title(t) is False, t


def test_detect_category():
    assert detect_category("DJ Night") == "dj"
    assert detect_category("Karaoke") == "karaoke"
    assert detect_category("Open Mic") == "open_mic"
    assert detect_category("Open-Mic Night") == "open_mic"
    assert detect_category("Trivia Night") == "trivia"
    assert detect_category("Duncan Crittenden") is None
    assert detect_category("Live Music") is None


# --- non-music community-calendar filtering ---------------------------------

def test_detect_non_music():
    assert detect_non_music("DeFuniak Springs Farmers Market") == "farmers_market"
    assert detect_non_music("Seaside Farmers Market") == "farmers_market"
    assert detect_non_music("Camp Helen State Park: Guided History Tour") == "guided_tour"
    assert detect_non_music("Camp Helen State Park Ranger-Guided Nature Hike") == "guided_tour"
    assert detect_non_music("Cars of 30A at Alys Beach") == "car_show"
    assert detect_non_music("IRONMAN Panama City Beach") == "sporting_event"
    assert detect_non_music("NAS Pensacola U.S. Blue Angels Homecoming Air Show") == "air_show"
    assert detect_non_music("Seeing Red Wine Festival: Battle of Somms") == "wine_festival"
    assert detect_non_music("Seeing Red Wine Festival") == "wine_festival"
    assert detect_non_music("Mountainfilm on Tour") == "film_festival"
    assert detect_non_music("Eggs on the Beach") == "eggfest"
    assert detect_non_music("Duncan Crittenden") is None
    assert detect_non_music("Live Music") is None
    assert detect_non_music(None) is None


def test_detect_non_music_newly_added_patterns():
    # Real listings that slipped through as blank-venue dashboard entries
    # before these patterns were added/widened.
    assert detect_non_music("Pensacola Beach Airshow: US Navy Blue Angels") == "air_show"
    assert detect_non_music("30A Thanksgiving Day Run") == "sporting_event"
    assert detect_non_music("Walton County Fair") == "county_fair"
    assert detect_non_music("Harvest Wine & Food Festival: Celebrity Winemakers Dinners") == "wine_festival"
    assert detect_non_music("Harvest Wine & Food Festival: Harvest After Dark") == "wine_festival"
    assert detect_non_music("Rosemary Beach Uncorked") == "wine_tasting"
    assert detect_non_music("Seaside Prize") == "award_ceremony"
    # An art market and a touring community-theater play -- both non-music,
    # both slipped through onto the live dashboard as junk rows (the play
    # even duplicated across two venues/nights) before these were added.
    assert detect_non_music("Shack's Sunday Art Market") == "art_market"
    assert detect_non_music("Grit and Grace: Holding Our Own") == "community_theater"
    # Real music events that must NOT be caught by the widened patterns.
    assert detect_non_music("Here Comes the Sun Summer Concert Series at Rosemary Beach") is None
    assert detect_non_music("Rockin' In Paradise with Styx + Friends") is None
    assert detect_non_music("Shinedown's Lunatic Ball Beach Weekend") is None


def test_detect_non_music_more_community_calendar_categories():
    # A batch of real slipped-through junk rows, each verified against its
    # actual SoWal description before adding a pattern -- not guessed from
    # the title alone (several title-plausible guesses below turned out to
    # be wrong, see the "must NOT catch" block).
    assert detect_non_music("Free Bingo at Stinky's Bait Shack") == "bingo"
    assert detect_non_music("Bingo Nights Rooftop Heights at Hotel Effie") == "bingo"
    assert detect_non_music("Queens of the Tiles: Mahjong at Ovide") == "game_night"
    assert detect_non_music("AJ's Tin Cup Classic") == "golf"
    assert detect_non_music("Sacred Heart Annual Charity Golf Classic") == "golf"
    assert detect_non_music("Grayton Locals Market") == "locals_market"
    assert detect_non_music("Kids' Night Out") == "kids_activity"
    assert detect_non_music("Pumpkin Carving & Costume Contest at Scratch Biscuit Kitchen") == "kids_activity"
    assert detect_non_music("Seaside Halloweener Derby & Costume Contest") == "kids_activity"
    assert detect_non_music("October Film Series at Eden Gardens State Park") == "film_festival"
    assert detect_non_music("Ranger Led Hike at Eden Gardens State Park") == "guided_tour"
    assert detect_non_music("Ellie Biscuit 20 &10-Mile Trail Race") == "sporting_event"
    assert detect_non_music("In the Woods 30A 50K") == "sporting_event"
    assert detect_non_music("Emerald Coast Foundation's Children's Charity Poker Run") == "sporting_event"
    # ECTC (Emerald Coast Theatre Company) productions and other staged
    # theater/ballet -- confirmed via venue ("Emerald Coast Theatre Company")
    # and description (Ballet Pensacola, Panama City Theatre), not music.
    assert detect_non_music("Come From Away at ECTC") == "theater_production"
    assert detect_non_music("9 to 5: The Musical at ECTC") == "theater_production"
    assert detect_non_music("Grease - The Musical") == "theater_production"
    assert detect_non_music("The Nutcracker at Seaside") == "theater_production"
    # Narrow on purpose: "Sounds Like Summer" alternates real live-music
    # nights with scripted children's plays under one series title, so only
    # the play instances should match.
    assert detect_non_music("Sounds Like Summer: Children's Play") == "childrens_play"


def test_detect_non_music_does_not_catch_themed_parties_with_real_booked_music():
    # Confirmed via actual SoWal descriptions: titles that read like generic
    # non-music party/festival branding but turned out to have a real named
    # act or explicit live music booked -- must NOT be swept up by a broad
    # "festival"/"bash"/"party" guess.
    assert detect_non_music("Havana Nights  at The Pearl Hotel") is None
    assert detect_non_music("Baytowne Beer Festival Backyard Bash") is None
    assert detect_non_music("30A BBQ Festival") is None
    assert detect_non_music("Barktoberfest") is None
    assert detect_non_music("Labor Day Block Party at WaterColor Package Store") is None


def test_detect_non_music_does_not_catch_real_music_festivals_with_no_single_act():
    # "Panama City Songwriters Festival" is real (SoWal description:
    # "Featuring original songs by local, regional and national
    # musicians... intimate music venues") -- same shape as "Moon Crush:
    # Oldies" elsewhere in this file. Neither must be caught here.
    assert detect_non_music("Panama City Songwriters Festival") is None
    assert detect_non_music("Moon Crush: Oldies") is None


def test_classify_non_music_excluded_even_with_a_named_looking_description():
    # unlike DJ/karaoke, a farmers market's description was never going to
    # name a musical act -- no description fallback attempted at all
    c = classify_performer("DeFuniak Springs Farmers Market",
                            "featuring live music by Jim Couch")
    assert c["performer"] is None
    assert c["performer_status"] == "category"
    assert c["resolved"] is False
    assert c["event_category"] == "farmers_market"
    assert c["extraction_method"] == "non_music"


def test_classify_guided_tour_excluded():
    c = classify_performer("Camp Helen State Park: Guided History Tour")
    assert c["performer"] is None
    assert c["performer_status"] == "category"
    assert c["event_category"] == "guided_tour"


def test_classify_car_show_excluded():
    c = classify_performer("Cars of 30A at Alys Beach")
    assert c["performer"] is None
    assert c["performer_status"] == "category"
    assert c["event_category"] == "car_show"


def test_classify_real_festival_with_no_single_named_act_is_not_flagged_non_music():
    # "Moon Crush: Oldies" is a real multi-artist festival (Old Dominion,
    # Darius Rucker, Flo Rida, ...) -- not a farmers-market-style listing.
    # It has no single named act in the title, so it's neither "named" nor
    # excluded as non-music; falls through to the whole-title-as-performer
    # branch like any other title with no cue, same as before this change.
    assert detect_non_music("Moon Crush: Oldies") is None


# --- conservative description extraction -----------------------------------

def test_extract_strong_indicators():
    assert extract_performer_from_description("Join us featuring Bill Garrett tonight") == "Bill Garrett"
    assert extract_performer_from_description("feat. Casey Kearney") == "Casey Kearney"
    assert extract_performer_from_description("ft. Gage Cowart on the deck") == "Gage Cowart"
    assert extract_performer_from_description("The venue presents Stevie Monce") == "Stevie Monce"
    assert extract_performer_from_description("performance by Nate Kelly") == "Nate Kelly"
    assert extract_performer_from_description("music by Zoe Walega") == "Zoe Walega"


def test_with_only_when_clearly_an_act():
    assert extract_performer_from_description("An evening with Harrison Prentice") == "Harrison Prentice"


def test_with_rejects_ordinary_prose():
    for s in ["brunch with friends", "join us with family", "music with dinner",
              "come hang with us"]:
        assert extract_performer_from_description(s) is None, s


def test_extract_rejects_generic_and_empty():
    assert extract_performer_from_description("featuring live music all night") is None
    assert extract_performer_from_description("") is None
    assert extract_performer_from_description(None) is None


# --- classify_performer end to end -----------------------------------------

def test_classify_named_title():
    c = classify_performer("Duncan Crittenden @ Local Catch")
    assert c["performer"] == "Duncan Crittenden"
    assert c["performer_status"] == "named"
    assert c["resolved"] is True
    assert c["event_category"] == "live_music"
    assert c["extraction_method"] == "title"


def test_classify_generic_unresolved():
    c = classify_performer("Live Music @ North Beach Social")
    assert c["performer"] is None
    assert c["performer_status"] == "unresolved"
    assert c["resolved"] is False
    assert c["event_category"] == "live_music"
    assert c["extraction_method"] == "unresolved"


def test_classify_generic_recovered_from_description():
    c = classify_performer("Brunch & Live Music @ Local Catch",
                           "Join us featuring Bill Garrett on the patio")
    assert c["performer"] == "Bill Garrett"
    assert c["performer_status"] == "named"
    assert c["resolved"] is True
    assert c["extraction_method"] == "description"


def test_classify_category_without_name():
    c = classify_performer("DJ Night @ Chiringo")
    assert c["performer"] is None
    assert c["performer_status"] == "category"
    assert c["event_category"] == "dj"
    assert c["resolved"] is False
    assert c["extraction_method"] == "category"


def test_classify_category_with_named_performer():
    # A category event names an act -> it becomes a named observation.
    c = classify_performer("DJ Night @ Chiringo", "featuring Zack Miller")
    assert c["performer"] == "Zack Miller"
    assert c["performer_status"] == "named"
    assert c["event_category"] == "dj"
    assert c["resolved"] is True


# --- lineup date handling ---------------------------------------------------

def test_parse_lineup_date_explicit_year():
    assert parse_lineup_date("Monday, July 06, 2026") == "2026-07-06"


def test_parse_lineup_date_infers_year_from_page_context():
    assert parse_lineup_date("Monday, July 06", page_year=2026) == "2026-07-06"


def test_parse_lineup_date_rejects_without_confident_date():
    assert parse_lineup_date("sometime next week", page_year=2026) is None
    assert parse_lineup_date("Gage Cowart", page_year=2026) is None


# --- multi-observation (lineup) parsing ------------------------------------

_LINEUP_HTML = """
<html><body>
  <h1>Weekly Live Music Series @ AJ's Grayton</h1>
  <p>When: Monday, July 06, 2026</p>
  <p>Where: AJ's Grayton</p>
  <table>
    <tr><td>Monday, July 06, 2026</td><td>Gage Cowart</td><td>6:00 pm</td></tr>
    <tr><td>Tuesday, July 07, 2026</td><td>Nate Kelly</td><td>7:00 pm</td></tr>
    <tr><td>Wednesday, July 08, 2026</td><td>featuring Zack Miller</td><td>6:00 pm</td></tr>
  </table>
</body></html>
"""


def test_lineup_yields_one_observation_per_row():
    soup = BeautifulSoup(_LINEUP_HTML, "lxml")
    crawler = SoWalCrawler()
    obs = crawler._parse_lineup(soup, "AJ's Grayton", 2026, "https://sowal.com/event/x", "Weekly Live Music Series")
    assert len(obs) == 3
    by_perf = {o["performer"]: o for o in obs}
    assert set(by_perf) == {"Gage Cowart", "Nate Kelly", "Zack Miller"}
    # each row keeps its OWN date (page-level date is not smeared across rows)
    assert by_perf["Gage Cowart"]["date"] == "2026-07-06"
    assert by_perf["Nate Kelly"]["date"] == "2026-07-07"
    assert by_perf["Zack Miller"]["date"] == "2026-07-08"
    # a "featuring X" cell resolves to the credited act
    assert by_perf["Zack Miller"]["extraction_method"] == "lineup"
    assert all(o["performer_status"] == "named" and o["resolved"] for o in obs)


def test_lineup_row_without_confident_date_marked_unresolved():
    html = """
    <html><body>
      <h1>Series @ Venue</h1>
      <table>
        <tr><td>Monday, July 06, 2026</td><td>Gage Cowart</td><td>6:00 pm</td></tr>
        <tr><td>date TBA</td><td>Nate Kelly</td><td>7:00 pm</td></tr>
      </table>
    </body></html>
    """
    soup = BeautifulSoup(html, "lxml")
    obs = SoWalCrawler()._parse_lineup(soup, "Venue", None, "u", "Series")
    assert len(obs) == 2
    named = [o for o in obs if o["performer_status"] == "named"]
    unresolved = [o for o in obs if o["performer_status"] == "unresolved"]
    # The row with a confident date resolves; the one without is unresolved (date None).
    assert len(named) == 1 and named[0]["performer"] == "Gage Cowart"
    assert named[0]["date"] == "2026-07-06"
    assert len(unresolved) == 1
    assert unresolved[0]["date"] is None and unresolved[0]["resolved"] is False


# SoWal's "Explore SoWal" recommendation widget (Drupal view ID
# 'date_calendar_lists') is a stack of single-row <table class="views-table">
# elements listing OTHER events on the site (e.g. this same series' other
# upcoming dates), each with no date cell of its own. It was initially
# mistaken for a page-position thing (it renders inside a "second column"
# wrapper) -- but that same column also holds genuine event-detail panes on
# some pages, so only the widget's own pane identity (its view/pane CSS
# classes) reliably distinguishes it. Scanning the whole page for <tr>
# misreads it as a multi-row lineup and manufactures a garbage "unresolved,
# date=None" observation per widget row (one single real event turned into
# many duplicates).
_CALENDAR_WIDGET_HTML = """
<html><body>
  <h1>30Avenue Summer Concert Series @ 30Avenue</h1>
  <p>When: Wednesday, July 15, 2026</p>
  <p>Time: 6:00 pm to 9:00 pm</p>
  <p>Where: 30Avenue</p>
  <div class="panel-pane pane-views-panes pane-date-calendar-lists-panel-pane-11">
    <div class="view view-date-calendar-lists view-id-date_calendar_lists">
      <table class="views-table"><tr><td>6:00 pm</td><td>30Avenue Summer Concert Series</td><td>30Avenue</td></tr></table>
      <table class="views-table"><tr><td>6:00 pm</td><td>30Avenue Summer Concert Series</td><td>30Avenue</td></tr></table>
      <table class="views-table"><tr><td>6:00 pm</td><td>30Avenue Summer Concert Series</td><td>30Avenue</td></tr></table>
    </div>
  </div>
</body></html>
"""


def test_in_calendar_widget_true_for_widget_rows():
    soup = BeautifulSoup(_CALENDAR_WIDGET_HTML, "lxml")
    trs = soup.find_all("tr")
    assert len(trs) == 3
    assert all(SoWalCrawler._in_calendar_widget(tr) for tr in trs)


def test_in_calendar_widget_false_for_ordinary_table():
    html = "<html><body><h1>Series @ Venue</h1><table><tr><td>x</td></tr></table></body></html>"
    soup = BeautifulSoup(html, "lxml")
    tr = soup.find("tr")
    assert SoWalCrawler._in_calendar_widget(tr) is False


def test_parse_lineup_ignores_calendar_widget_rows():
    soup = BeautifulSoup(_CALENDAR_WIDGET_HTML, "lxml")
    obs = SoWalCrawler()._parse_lineup(soup, "30Avenue", 2026, "https://sowal.com/event/x", "Series")
    assert obs == []


# --- partitioning -----------------------------------------------------------

def test_partition_observations():
    obs = [
        {"performer": "A", "performer_status": "named", "resolved": True},
        {"performer": None, "performer_status": "unresolved", "resolved": False},
        {"performer": None, "performer_status": "category", "resolved": False},
        {"performer": "B", "performer_status": "named", "resolved": True},
    ]
    parts = partition_observations(obs)
    assert [o["performer"] for o in parts["named"]] == ["A", "B"]
    assert len(parts["unresolved"]) == 1
    assert len(parts["category"]) == 1


# --- extraction confidence by method (constraint 7) ------------------------

def _ev(method):
    return {"performer": "A", "venue": "V", "date": "2026-07-11",
            "time_start": "6:00 pm", "extraction_method": method}


def test_description_and_lineup_confidence_lower_than_title():
    title = extraction_confidence(_ev("title"))
    lineup = extraction_confidence(_ev("lineup"))
    desc = extraction_confidence(_ev("description"))
    assert title > lineup > desc
    # title-derived is unpenalised (same as no method at all)
    assert extraction_confidence(_ev("title")) == extraction_confidence(
        {k: v for k, v in _ev("title").items() if k != "extraction_method"})
