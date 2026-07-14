"""
tests/test_dashboard.py
Tests for the dumb dashboard renderer: it renders precomputed DB values
(confidence, provenance, conflicts) into the preserved shell.
"""
import sys
import tempfile
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
    assert "no unfilled placeholders", "PLACEHOLDER" not in html


def test_render_venue_links_to_google_maps():
    html, _ = _render_to_temp([
        {"performer": "A", "venue": "Chiringo", "date": "2026-07-11", "time_start": "6PM", "source": "sowal"},
    ])
    assert 'class="maplink"' in html
    assert "google.com/maps" in html
    assert "Get directions to Chiringo" in html


# --- venue region grouping + favorites (editable CSV, not code) -------------

def test_venue_group_known_venue(tmp_path):
    csv_file = tmp_path / "venue_groups.csv"
    csv_file.write_text("venue,group,favorite\nChiringo,West 30A,N\n", encoding="utf-8")
    meta = render._load_venue_meta(csv_file)
    assert render._venue_group("Chiringo", meta) == "West 30A"
    assert render._venue_group("CHIRINGO", meta) == "West 30A"  # case-insensitive


def test_venue_group_unlisted_or_blank_falls_back_to_other(tmp_path):
    csv_file = tmp_path / "venue_groups.csv"
    csv_file.write_text("venue,group,favorite\nSome Church,,N\n", encoding="utf-8")
    meta = render._load_venue_meta(csv_file)
    assert render._venue_group("Some Church", meta) == "Other"      # blank group in file
    assert render._venue_group("Never Listed", meta) == "Other"     # not in file at all
    assert render._venue_group(None, meta) == "Other"


def test_venue_group_missing_csv_defaults_everything_to_other(tmp_path):
    meta = render._load_venue_meta(tmp_path / "does_not_exist.csv")
    assert meta == {}
    assert render._venue_group("Chiringo", meta) == "Other"


def test_venue_favorite_yes_no_and_defaults(tmp_path):
    csv_file = tmp_path / "venue_groups.csv"
    csv_file.write_text(
        "venue,group,favorite\nChiringo,West 30A,Y\nOther Place,West 30A,N\nNo Flag,West 30A,\n",
        encoding="utf-8",
    )
    meta = render._load_venue_meta(csv_file)
    assert render._venue_favorite("Chiringo", meta) is True
    assert render._venue_favorite("chiringo", meta) is True   # case-insensitive
    assert render._venue_favorite("Other Place", meta) is False
    assert render._venue_favorite("No Flag", meta) is False   # blank cell -> not a favorite
    assert render._venue_favorite("Never Listed", meta) is False
    assert render._venue_favorite(None, meta) is False


def test_render_rows_carry_data_region_and_favorite():
    html, _ = _render_to_temp([
        {"performer": "A", "venue": "Chiringo", "date": "2026-07-11", "time_start": "6PM", "source": "sowal"},
    ])
    assert 'data-region="' in html
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


def test_render_rows_carry_data_performer_favorite():
    html, _ = _render_to_temp([
        {"performer": "A", "venue": "Chiringo", "date": "2026-07-11", "time_start": "6PM", "source": "sowal"},
    ])
    assert 'data-performer-favorite="' in html


def test_favorites_filter_matches_venue_or_performer_favorite():
    html, _ = _render_to_temp([
        {"performer": "A", "venue": "V", "date": "2026-07-11", "time_start": "6PM", "source": "seed"},
    ])
    # the ★ Favorites toggle includes a show if EITHER the venue or the
    # performer is marked favorite, in both the results renderer and the
    # dropdown-population pass
    assert "favOnly&&fv!=='Y'&&pfv!=='Y'" in html


def test_render_includes_region_and_favorites_filter_controls():
    html, _ = _render_to_temp([
        {"performer": "A", "venue": "V", "date": "2026-07-11", "time_start": "6PM", "source": "seed"},
    ])
    assert 'id="rf"' in html
    assert "All Regions" in html
    assert 'id="favbtn"' in html
    assert "★ Favorites" in html


def test_results_rebuild_on_favorites_toggle_and_clear():
    html, _ = _render_to_temp([
        {"performer": "A", "venue": "V", "date": "2026-07-11", "time_start": "6PM", "source": "seed"},
    ])
    # rr() is the single, re-callable renderer (replacing the old table +
    # separate Today card) and it skips non-favorite rows when the
    # favorites toggle is on.
    assert "function rr()" in html
    assert "favOnly&&fv!=='Y'" in html
    # both the favorites toggle and Clear rebuild the results view
    assert "bd();rr();" in html
    assert "sf('today');" in html      # Clear resets to the default filter


def test_results_also_filter_by_region():
    html, _ = _render_to_temp([
        {"performer": "A", "venue": "V", "date": "2026-07-11", "time_start": "6PM", "source": "seed"},
    ])
    assert "rf&&rg!==rf" in html
    # the region select rebuilds the results view too
    assert 'onchange="bd();rr()" aria-label="Filter by region"' in html


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


def test_build_marker_is_filled_and_visible_in_header():
    html, _ = _render_to_temp([
        {"performer": "A", "venue": "V", "date": "2026-07-11", "time_start": "6PM", "source": "seed"},
    ])
    # Server-side build stamp (fixed at generation time), distinct from the
    # client-side "Updated <today>" badge which always shows the viewer's
    # own current date and so can't reveal a stale cached page. Placed in
    # the header so it's visible without scrolling in any screenshot.
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
    # no narrow-viewport override dropping to a 2x2 grid.
    assert ".stats{display:grid;grid-template-columns:repeat(4,1fr)" in html
    assert "grid-template-columns:repeat(2,1fr)" not in html
