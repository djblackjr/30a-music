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
    # filter/sort JS, Today card container, and the Google Maps modal
    assert "<svg" in html
    assert 'id="q"' in html
    assert 'id="b-today"' in html
    assert "function go()" in html
    assert "function srt(" in html
    assert 'class="tn"' in html
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


def test_render_includes_region_and_favorites_filter_controls():
    html, _ = _render_to_temp([
        {"performer": "A", "venue": "V", "date": "2026-07-11", "time_start": "6PM", "source": "seed"},
    ])
    assert 'id="rf"' in html
    assert "All Regions" in html
    assert 'id="favbtn"' in html
    assert "★ Favorites" in html


def test_today_card_rebuilds_on_favorites_toggle_and_clear():
    html, _ = _render_to_temp([
        {"performer": "A", "venue": "V", "date": "2026-07-11", "time_start": "6PM", "source": "seed"},
    ])
    # Today is a named, re-callable function (not a run-once IIFE) that skips
    # non-favorite rows when the favorites toggle is on.
    assert "function bt()" in html
    assert "favOnly&&r.getAttribute('data-favorite')!=='Y'" in html
    # both the favorites toggle and Clear rebuild Today, not just the table
    assert "bd();go();bt();" in html
    assert "sf('up');bt();" in html


def test_today_card_also_filters_by_region():
    html, _ = _render_to_temp([
        {"performer": "A", "venue": "V", "date": "2026-07-11", "time_start": "6PM", "source": "seed"},
    ])
    assert "curR&&r.getAttribute('data-region')!==curR" in html
    # the region select rebuilds Today too, not just the table
    assert "bd();go();bt()" in html


def test_mobile_table_does_not_clip_the_external_brace():
    html, _ = _render_to_temp([
        {"performer": "A", "venue": "V", "date": "2026-07-11", "time_start": "6PM", "source": "seed"},
    ])
    # table{overflow:hidden} (desktop) is never reset in the mobile media
    # query, which clips the now/upcoming ::before brace (positioned
    # outside the card via a negative left offset) right back to the
    # table's edge -- undoing the "external brace" fix. Regression test
    # for that exact bug.
    assert ".wrap table{background:transparent;box-shadow:none;border:none;overflow:visible;}" in html
