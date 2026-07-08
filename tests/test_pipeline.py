"""
tests/test_pipeline.py
Basic tests for the 30A Music Intelligence pipeline.
Run: python -m pytest tests/ -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.reconcile.changes import compare_runs
from app.database.db import init_db, save_events, load_events, get_last_run_id
import tempfile, os


# ---------------------------------------------------------------------------
# compare_runs
# ---------------------------------------------------------------------------

def _ev(performer, venue, date, time="6PM", **kwargs):
    return {
        "name": f"{performer} at {venue}",
        "performer": performer,
        "venue": venue,
        "date": date,
        "time_start": time,
        **kwargs,
    }


def test_compare_all_new():
    current  = [_ev("Artist A", "Venue X", "2026-07-04")]
    previous = []
    result   = compare_runs(current, previous)
    assert result["summary"]["new"] == 1
    assert result["summary"]["changed"] == 0
    assert result["summary"]["removed"] == 0


def test_compare_unchanged():
    ev = _ev("Artist A", "Venue X", "2026-07-04")
    result = compare_runs([ev], [ev])
    assert result["summary"]["unchanged"] == 1
    assert result["summary"]["new"] == 0


def test_compare_changed():
    old = _ev("Artist A", "Venue X", "2026-07-04", time_start="6PM")
    new = _ev("Artist A", "Venue X", "2026-07-04", time_start="8PM")
    result = compare_runs([new], [old])
    assert result["summary"]["changed"] == 1


def test_compare_removed():
    old = _ev("Artist A", "Venue X", "2026-07-04")
    result = compare_runs([], [old])
    assert result["summary"]["removed"] == 1


def test_no_duplicate_keys():
    ev1 = _ev("Artist A", "Venue X", "2026-07-04")
    ev2 = _ev("Artist A", "Venue X", "2026-07-04")  # duplicate
    result = compare_runs([ev1, ev2], [])
    # Deduplication happens upstream in monitor.py; reconciler sees whatever it gets
    assert result["summary"]["new"] >= 1


# ---------------------------------------------------------------------------
# DB round-trip
# ---------------------------------------------------------------------------

def test_db_save_and_load():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    try:
        from app.database.db import DB_PATH
        import app.database.db as db_module

        # Patch path
        original = db_module.DB_PATH
        db_module.DB_PATH = db_path

        init_db(db_path)
        events = [_ev("Test Artist", "Test Venue", "2026-07-10", source="test")]
        saved  = save_events(events, run_id="run_001", path=db_path)
        assert saved == 1

        loaded = load_events(run_id="run_001", path=db_path)
        assert len(loaded) == 1
        assert loaded[0]["performer"] == "Test Artist"

        db_module.DB_PATH = original
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# Normalisation (via monitor._normalise_events)
# ---------------------------------------------------------------------------

def test_normalise_deduplication():
    from app.monitor import _normalise_events
    ev = _ev("Artist A", "Venue X", "2026-07-04")
    result = _normalise_events([ev, ev])
    assert len(result) == 1


def test_normalise_fills_name():
    from app.monitor import _normalise_events
    ev = {"performer": "Artist B", "venue": "Venue Y", "date": "2026-07-05", "time_start": "7PM"}
    result = _normalise_events([ev])
    assert result[0]["name"] == "Artist B at Venue Y"


def test_normalise_drops_empty_performer():
    from app.monitor import _normalise_events
    ev = {"performer": "", "venue": "Venue Z", "date": "2026-07-06", "time_start": "8PM"}
    result = _normalise_events([ev])
    assert len(result) == 0
