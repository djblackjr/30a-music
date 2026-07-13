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
