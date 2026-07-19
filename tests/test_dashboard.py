"""
tests/test_dashboard.py
Tests for the dumb dashboard renderer: it renders precomputed DB values
(confidence, provenance, conflicts) into the preserved shell.
"""
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database.db import init_db, record_run, save_events
from app.normalize import normalize_events
from app.dashboard import render


def _render_to_temp(raw_events):
    dbf = Path(tempfile.mktemp(suffix=".db"))
    outf = Path(tempfile.mktemp(suffix=".html"))
    init_db(dbf)
    events = normalize_events(raw_events)
    save_events(events, run_id="t1", path=dbf)
    record_run("t1", len(events), path=dbf)
    render.generate(out_path=outf, run_id="t1", path=dbf)
    html = outf.read_text()
    dbf.unlink(); outf.unlink()
    return html, events


def test_report_table_is_clean_and_provenance_is_in_the_detail_row():
    html, events = _render_to_temp([
        {"performer": "A", "venue": "V", "date": "2026-07-11", "time_start": "6PM", "source": "sowal"},
        {"performer": "A", "venue": "V", "date": "2026-07-11", "time_start": "6PM", "source": "venue"},
    ])
    assert len(events) == 1                       # two sources -> one event
    # the report itself carries no Sources/Confidence columns
    assert "<th scope=\"col\">Sources</th>" not in html
    assert "<th scope=\"col\">Confidence</th>" not in html
    # provenance lives in the expandable detail row instead
    assert 'class="exp"' in html
    assert "sowal" in html and "venue" in html
    assert 'class="cf' in html                    # confidence shown in the detail


def test_render_shows_conflict():
    html, events = _render_to_temp([
        {"performer": "A", "venue": "V", "date": "2026-07-11", "time_start": "6PM", "source": "venue"},
        {"performer": "A", "venue": "V", "date": "2026-07-11", "time_start": "8PM", "source": "instagram"},
    ])
    assert events[0]["conflict_flag"] == 1
    assert "⚠" in html
    assert "Time mismatch" in html


def test_render_preserves_shell():
    html, _ = _render_to_temp([
        {"performer": "A", "venue": "V", "date": "2026-07-11", "time_start": "6PM", "source": "seed"},
    ])
    # the hand-built design is preserved: corridor map, search, date filters,
    # filter/sort JS, the date-grouped results renderer, and the Google Maps modal
    assert "<svg" in html
    assert 'id="q"' in html
    assert 'id="b-today"' in html
    assert "function rr()" in html
    assert "function srt(" in html
    assert 'class="tn"' in html       # emitted by rr() for each date group
    assert 'id="mm"' in html          # directions modal
    assert "PLACEHOLDER" not in html, "no unfilled placeholders"


def test_render_venue_links_to_google_maps():
    html, _ = _render_to_temp([
        {"performer": "A", "venue": "Chiringo", "date": "2026-07-11", "time_start": "6PM", "source": "sowal"},
    ])
    assert 'class="maplink"' in html
    assert "google.com/maps" in html
    assert "Get directions to Chiringo" in html


# --- venue favorites (editable flat CSV, not code) --------------------------

def test_venue_favorite_listed_and_defaults(tmp_path):
    csv_file = tmp_path / "venue_groups.csv"
    csv_file.write_text("venue\nChiringo\n", encoding="utf-8")
    favorites = render._load_favorite_venues(csv_file)
    assert render._venue_favorite("Chiringo", favorites) is True
    assert render._venue_favorite("chiringo", favorites) is True   # case-insensitive
    assert render._venue_favorite("Other Place", favorites) is False  # not listed
    assert render._venue_favorite(None, favorites) is False


def test_venue_favorite_missing_csv_defaults_everything_to_false(tmp_path):
    favorites = render._load_favorite_venues(tmp_path / "does_not_exist.csv")
    assert favorites == set()
    assert render._venue_favorite("Chiringo", favorites) is False


def test_render_rows_carry_data_favorite():
    html, _ = _render_to_temp([
        {"performer": "A", "venue": "Chiringo", "date": "2026-07-11", "time_start": "6PM", "source": "sowal"},
    ])
    assert 'data-favorite="' in html


# --- performer favorites (app/dashboard/artists.csv, same pattern as
# venue_groups.csv) ---------------------------------------------------------

def test_performer_favorite_yes_no_and_defaults(tmp_path):
    csv_file = tmp_path / "artists.csv"
    csv_file.write_text(
        "performer,favorite\nJim Couch,Y\nOther Act,N\nNo Flag,\n",
        encoding="utf-8",
    )
    meta = render._load_performer_meta(csv_file)
    assert render._performer_favorite("Jim Couch", meta) is True
    assert render._performer_favorite("jim couch", meta) is True   # case-insensitive
    assert render._performer_favorite("Other Act", meta) is False
    assert render._performer_favorite("No Flag", meta) is False    # blank cell -> not a favorite
    assert render._performer_favorite("Never Listed", meta) is False
    assert render._performer_favorite(None, meta) is False


def test_performer_favorite_missing_csv_defaults_everything_to_false():
    meta = render._load_performer_meta(Path("/tmp/does_not_exist_artists.csv"))
    assert meta == {}
    assert render._performer_favorite("Jim Couch", meta) is False


def test_favorite_performer_names_lists_every_favorite_sorted_regardless_of_bookings(tmp_path):
    # the roster must include a favorite with zero current shows -- this is
    # the exact bug the ★ Artists dropdown had (only listed names that had
    # an upcoming <tr> row, silently dropping favorites with nothing booked)
    csv_file = tmp_path / "artists.csv"
    csv_file.write_text(
        "performer,favorite\nZoe Walega,Y\nAn Act With No Shows,Y\nOther Act,N\nNo Flag,\n",
        encoding="utf-8",
    )
    names = render._favorite_performer_names(csv_file)
    assert names == ["An Act With No Shows", "Zoe Walega"]   # sorted, case-insensitive


def test_favorite_performer_names_missing_csv_returns_empty_list():
    assert render._favorite_performer_names(Path("/tmp/does_not_exist_artists.csv")) == []


def test_favorite_venue_names_lists_every_venue_in_file_sorted(tmp_path):
    csv_file = tmp_path / "venue_groups.csv"
    csv_file.write_text(
        "venue\nZebra Lounge\nAJ's Grayton Beach\n",
        encoding="utf-8",
    )
    names = render._favorite_venue_names(csv_file)
    assert names == ["AJ's Grayton Beach", "Zebra Lounge"]   # sorted, case-insensitive


def test_favorite_venue_names_missing_csv_returns_empty_list():
    assert render._favorite_venue_names(Path("/tmp/does_not_exist_venue_groups.csv")) == []


def test_render_embeds_full_favorites_rosters_as_js_arrays(tmp_path):
    html, _ = _render_to_temp([
        {"performer": "A", "venue": "V", "date": "2026-07-11", "time_start": "6PM", "source": "seed"},
    ])
    assert "var ALL_FAV_VENUES=" in html
    assert "var ALL_FAV_ARTISTS=" in html
    assert "FAV_VENUES_PLACEHOLDER" not in html
    assert "FAV_ARTISTS_PLACEHOLDER" not in html


def test_render_rows_carry_data_performer_favorite():
    html, _ = _render_to_temp([
        {"performer": "A", "venue": "Chiringo", "date": "2026-07-11", "time_start": "6PM", "source": "sowal"},
    ])
    assert 'data-performer-favorite="' in html


def test_favorites_filters_combine_like_the_other_filters_and_and_not_or():
    html, _ = _render_to_temp([
        {"performer": "A", "venue": "V", "date": "2026-07-11", "time_start": "6PM", "source": "seed"},
    ])
    # The Venues and Artists dropdowns are independent multi-selects that
    # each narrow the results further when a choice is checked — a
    # non-empty selection in BOTH requires the venue to be in the checked
    # set AND the performer to be in the checked set (intersection), not
    # either one (union).
    assert "if(selVenues.size&&!selVenues.has(v))inc=false;" in html
    assert "if(selArtists.size&&!selArtists.has(a))inc=false;" in html


def test_render_includes_venue_artist_filter_controls():
    html, _ = _render_to_temp([
        {"performer": "A", "venue": "V", "date": "2026-07-11", "time_start": "6PM", "source": "seed"},
    ])
    # One dropdown each for venues and artists -- the full roster, favorites
    # starred and sorted first. No separate favorites-only shortcut.
    assert 'id="allVenueBtn"' in html
    assert ">Venues ▾</button>" in html
    assert 'id="allArtistBtn"' in html
    assert ">Artists ▾</button>" in html
    assert 'id="allVenuePanel"' in html
    assert 'id="allArtistPanel"' in html
    assert 'id="favVenueBtn"' not in html
    assert 'id="favArtistBtn"' not in html
    assert "function _favVenueSet()" in html
    assert "function _favArtistSet()" in html
    # the all-list panels star favorites within the full roster
    assert "favSet.has(n)" in html


def test_render_includes_featured_hero_section():
    html, _ = _render_to_temp([
        {"performer": "A", "venue": "V", "date": "2026-07-11", "time_start": "6PM", "source": "seed"},
    ])
    assert "Tonight’s best live music in 30A" in html
    assert 'class="hero-panel"' in html
    assert "Featured venue" in html


# --- hero "Featured venue" card: real data, not the old hardcoded stub -----

def _d(offset_days):
    return (datetime.now() + timedelta(days=offset_days)).strftime("%Y-%m-%d")


def _hero_chunk(html):
    # The hero-side-venue/-meta divs sit right after the "Featured venue"
    # label and nothing else on the page uses that class, so slicing there
    # isolates the hero card's own text from the (also server-rendered)
    # table rows for every other event further down the page.
    return html.split('class="hero-side-venue"')[1][:300]


def test_hero_features_the_soonest_upcoming_event_not_a_later_one():
    # Neither event is a favorite, so both land in the same (lowest) tier --
    # date is what breaks the tie here, same as before tier became primary.
    html, _ = _render_to_temp([
        {"performer": "Later Act", "venue": "Later Venue", "date": _d(5),
         "time_start": "7PM", "source": "venue"},
        {"performer": "Soonest Act", "venue": "Soonest Venue", "date": _d(1),
         "time_start": "6PM", "source": "venue"},
    ])
    chunk = _hero_chunk(html)
    assert "Soonest Venue" in chunk
    assert "Later Venue" not in chunk


def test_hero_prefers_a_later_favorite_combo_over_a_sooner_non_favorite_show(monkeypatch):
    # Favorite tier outranks date entirely -- a favorite-artist +
    # favorite-venue show always wins the hero slot, even over something
    # happening sooner that isn't a favorite at all.
    monkeypatch.setattr(render, "_load_favorite_venues", lambda *a, **k: {"combo venue"})
    monkeypatch.setattr(render, "_load_performer_meta", lambda *a, **k: {"combo act": True})
    html, _ = _render_to_temp([
        {"performer": "Sooner Nobody", "venue": "Sooner Venue", "date": _d(1),
         "time_start": "6PM", "source": "venue"},
        {"performer": "Combo Act", "venue": "Combo Venue", "date": _d(8),
         "time_start": "7PM", "source": "venue"},
    ])
    chunk = _hero_chunk(html)
    assert "Combo Venue" in chunk
    assert "Sooner Venue" not in chunk


def test_hero_prefers_higher_confidence_event_on_a_tied_date():
    tied_date = _d(2)
    html, _ = _render_to_temp([
        # Neither venue is in venue_groups.csv, so both land in the same
        # favorite tier and confidence -- "venue" source is high-trust (0.95)
        # vs. an unlisted source's 0.5 default trust -- is what should decide
        # which one heads the hero card.
        {"performer": "Unverified Act", "venue": "Unverified Venue", "date": tied_date,
         "time_start": "6PM", "source": "some_random_blog"},
        {"performer": "Verified Act", "venue": "Verified Venue", "date": tied_date,
         "time_start": "8PM", "source": "venue"},
    ])
    chunk = _hero_chunk(html)
    assert "Verified Venue" in chunk
    assert "High confidence" in chunk


def test_hero_prefers_favorite_artist_plus_favorite_venue_combo_over_either_alone(monkeypatch):
    # _featured_event() calls _load_favorite_venues()/_load_performer_meta()
    # with no args, so patching the VENUE_FAVORITES_CSV/ARTISTS_CSV module
    # constants wouldn't work here -- those are only read once, as the
    # functions' default *parameter* values, at import time. Patching the
    # loader functions themselves is what actually redirects the call.
    monkeypatch.setattr(render, "_load_favorite_venues", lambda *a, **k: {
        "combo venue", "venue-only venue",
    })
    monkeypatch.setattr(render, "_load_performer_meta", lambda *a, **k: {
        "combo act": True,
        "artist-only act": True,
        "venue-only act": False,
    })
    tied_date = _d(3)
    html, _ = _render_to_temp([
        # Lower confidence than the other two, but the combo of favorite
        # artist + favorite venue must still win the hero slot outright.
        {"performer": "Combo Act", "venue": "Combo Venue", "date": tied_date,
         "time_start": "6PM", "source": "some_random_blog"},
        {"performer": "Artist-Only Act", "venue": "Artist-Only Venue", "date": tied_date,
         "time_start": "7PM", "source": "venue"},
        {"performer": "Venue-Only Act", "venue": "Venue-Only Venue", "date": tied_date,
         "time_start": "8PM", "source": "venue"},
    ])
    chunk = _hero_chunk(html)
    assert "Combo Venue" in chunk
    assert "Artist-Only Venue" not in chunk
    assert "Venue-Only Venue" not in chunk


def test_hero_headlines_the_performer_name():
    # The hero's big display headline is the performer, not the venue --
    # the venue/time back it up in the glass side panel instead.
    html, _ = _render_to_temp([
        {"performer": "Combo Act", "venue": "V", "date": _d(2),
         "time_start": "6PM", "source": "venue"},
    ])
    assert '<h2 class="hero-title">Combo Act</h2>' in html


def test_hero_shows_favorite_badges_matching_its_own_tier(monkeypatch):
    monkeypatch.setattr(render, "_load_favorite_venues", lambda *a, **k: {"combo venue"})
    monkeypatch.setattr(render, "_load_performer_meta", lambda *a, **k: {"combo act": True})
    html, _ = _render_to_temp([
        {"performer": "Combo Act", "venue": "Combo Venue", "date": _d(2),
         "time_start": "6PM", "source": "venue"},
    ])
    assert "★ Favorite artist" in html
    assert "★ Favorite venue" in html


def test_hero_shows_no_badges_when_neither_artist_nor_venue_is_a_favorite(monkeypatch):
    monkeypatch.setattr(render, "_load_favorite_venues", lambda *a, **k: set())
    monkeypatch.setattr(render, "_load_performer_meta", lambda *a, **k: {})
    html, _ = _render_to_temp([
        {"performer": "Nobody Special", "venue": "Nowhere Venue", "date": _d(2),
         "time_start": "6PM", "source": "venue"},
    ])
    assert '<div class="badge-row"></div>' in html


def test_hero_prefers_favorite_artist_over_favorite_venue_when_not_combined(monkeypatch):
    monkeypatch.setattr(render, "_load_favorite_venues", lambda *a, **k: {
        "venue-only venue",
    })
    monkeypatch.setattr(render, "_load_performer_meta", lambda *a, **k: {
        "artist-only act": True,
        "venue-only act": False,
    })
    tied_date = _d(3)
    html, _ = _render_to_temp([
        # Venue-Only has the higher confidence source, but a favorite artist
        # (even at a non-favorite venue) outranks a favorite venue alone.
        {"performer": "Venue-Only Act", "venue": "Venue-Only Venue", "date": tied_date,
         "time_start": "6PM", "source": "venue"},
        {"performer": "Artist-Only Act", "venue": "Artist-Only Venue", "date": tied_date,
         "time_start": "7PM", "source": "some_random_blog"},
    ])
    chunk = _hero_chunk(html)
    assert "Artist-Only Venue" in chunk
    assert "Venue-Only Venue" not in chunk


def test_hero_labels_todays_event_as_today():
    html, _ = _render_to_temp([
        {"performer": "A", "venue": "Tonight Venue", "date": _d(0),
         "time_start": "6PM", "source": "venue"},
    ])
    chunk = _hero_chunk(html)
    assert "Tonight Venue" in chunk
    assert "Today" in chunk


def test_hero_falls_back_gracefully_with_no_upcoming_events():
    # every event is past-dated -- _featured_event() must not crash or pick
    # a stale show, and no HERO_*_PLACEHOLDER token should leak into the page.
    html, _ = _render_to_temp([
        {"performer": "A", "venue": "V", "date": _d(-30), "time_start": "6PM", "source": "venue"},
    ])
    assert "No shows scheduled" in html
    assert "HERO_VENUE_PLACEHOLDER" not in html
    assert "HERO_META_PLACEHOLDER" not in html


def test_favorites_panel_includes_select_all_toggle():
    html, _ = _render_to_temp([
        {"performer": "A", "venue": "V", "date": "2026-07-11", "time_start": "6PM", "source": "seed"},
    ])
    # _favPanelHtml() renders a Select All / Deselect All button above the
    # checklist, and a click handler toggles the whole set at once rather
    # than requiring one tap per favorite.
    assert 'class="favdd-selectall"' in html
    assert "Select All" in html
    assert "Deselect All" in html
    assert "closest('.favdd-selectall')" in html


def test_results_rebuild_on_favorites_selection_and_clear():
    html, _ = _render_to_temp([
        {"performer": "A", "venue": "V", "date": "2026-07-11", "time_start": "6PM", "source": "seed"},
    ])
    # rr() is the single, re-callable renderer (replacing the old table +
    # separate Today card) and it skips rows that don't match whichever
    # favorites selection(s) are checked.
    assert "function rr()" in html
    assert "selVenues.size&&!selVenues.has(v)" in html
    # both a checkbox change and Select All/Deselect All rebuild the results view
    assert "document.addEventListener('change',function(e){" in html
    assert "sf('today');" in html      # Clear resets to the default filter


def test_startup_filter_defaults_to_today():
    html, _ = _render_to_temp([
        {"performer": "A", "venue": "V", "date": "2026-07-11", "time_start": "6PM", "source": "seed"},
    ])
    assert "df='today'" in html
    assert html.rstrip().endswith("sf('today');\n</script></body></html>")


def test_date_groups_render_with_date_header_and_favorite_star():
    html, _ = _render_to_temp([
        {"performer": "A", "venue": "V", "date": "2026-07-11", "time_start": "6PM", "source": "seed"},
    ])
    # each date-grouped box gets a header built from the date, and every
    # show card still carries the favorite star when the venue is a favorite
    assert "'Today — '" in html
    assert "tn-head" in html
    assert "fav-star" in html


def test_stats_counters_moved_to_bottom_of_page():
    html, _ = _render_to_temp([
        {"performer": "A", "venue": "V", "date": "2026-07-11", "time_start": "6PM", "source": "seed"},
    ])
    # stats now render after the map/legend, not immediately under the header
    assert html.index('class="map-card"') < html.index('class="stats"')
    assert html.index('class="stats"') < html.index("</main>")


def test_mobile_cards_suppress_the_desktop_first_cell_border():
    html, _ = _render_to_temp([
        {"performer": "A", "venue": "V", "date": "2026-07-11", "time_start": "6PM", "source": "seed"},
    ])
    # tr.now/tr.up td:first-child{border-left:...} (desktop only, unscoped)
    # still exists for the plain table view. A plain `.wrap td:first-child`
    # override is NOT enough to suppress it on mobile: that selector has
    # specificity (0,0,2,1) -- lower than the original's (0,0,2,2) (tr + td
    # element selectors beat .wrap + td), so CSS specificity lets the
    # desktop rule win regardless of source order or media-query nesting.
    # Verified live: this was the actual cause of a line that survived
    # several rounds of unrelated "fixes". The override must match the
    # original's tr.now/tr.up + td:first-child pattern with an added class
    # to be strictly more specific, not just present.
    assert ".wrap tr.now td:first-child,.wrap tr.up td:first-child{border-left:none;}" in html


def test_build_marker_is_filled_and_present():
    html, _ = _render_to_temp([
        {"performer": "A", "venue": "V", "date": "2026-07-11", "time_start": "6PM", "source": "seed"},
    ])
    # Server-side build stamp (fixed at generation time), distinct from the
    # client-side "Updated <today>" badge which always shows the viewer's
    # own current date and so can't reveal a stale cached page. Lives in
    # the page footer (below the stats) rather than the header.
    assert "BUILD_PLACEHOLDER" not in html
    assert "Build " in html


def test_pavilion_venue_badge_shows_shortened_name():
    html, _ = _render_to_temp([
        {"performer": "A", "venue": "The Pavilion at Watersound Town Center",
         "date": "2026-07-11", "time_start": "6PM", "source": "seed"},
    ])
    # Shortened text is for the badge label only -- filtering, directions,
    # and aria-labels still key off the full real venue name.
    assert "The Pavilion at WTC" in html
    assert 'data-venue="The Pavilion at Watersound Town Center"' in html
    assert "Get directions to The Pavilion at Watersound Town Center" in html


def test_stat_counters_stay_four_across_on_mobile():
    html, _ = _render_to_temp([
        {"performer": "A", "venue": "V", "date": "2026-07-11", "time_start": "6PM", "source": "seed"},
    ])
    # Total/Showing/Venues/Artists render as one row on every screen size --
    # no narrow-viewport override dropping .stats to a 2x2 grid. (Other
    # elements, like the .dr filter-button grid, legitimately use a 2-column
    # layout -- this only guards .stats specifically.)
    assert ".stats{display:grid;grid-template-columns:repeat(4,1fr)" in html
    assert html.count(".stats{") == 1
