"""
tests/test_accumulation.py
Cross-run observation accumulation: one canonical event per identity, with
observations from different runs/sources accumulating onto it so they
corroborate (rather than creating duplicate events and losing provenance).

Also covers time comparison — different sources format the same time
differently, which must NOT read as a conflict.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database.db import init_db, load_events, load_event_observations, upsert_events
from app.normalize import normalize_events
from app.normalize.provenance import start_minutes, _times_conflict


def _db():
    p = Path(tempfile.mktemp(suffix=".db"))
    init_db(p)
    return p


def _ev(source, time_start="6:00 pm", performer="A", venue="V", date="2026-07-15"):
    return {"performer": performer, "venue": venue, "date": date,
            "time_start": time_start, "source": source}


# --- time parsing / comparison ---------------------------------------------

def test_start_minutes_formats():
    assert start_minutes("6:00 pm") == 18 * 60
    assert start_minutes("6:00 - 9:00 PM") == 18 * 60      # range, start is 6 PM
    assert start_minutes("9:00 - 1:00 AM") == 21 * 60      # wraps: starts 9 PM
    assert start_minutes("12:00 - 4:00 PM") == 12 * 60     # noon
    assert start_minutes("After the Parade") is None


def test_same_time_different_format_is_not_a_conflict():
    assert _times_conflict("6:00 - 9:00 PM", "6:00 pm") is False


def test_genuinely_different_times_conflict():
    assert _times_conflict("6:00 pm", "6:30 pm") is True


# --- cross-run accumulation -------------------------------------------------

def test_second_source_corroborates_instead_of_duplicating():
    p = _db()
    upsert_events(normalize_events([_ev("dashboard_legacy", "6:00 - 9:00 PM")]), run_id="r1", path=p)
    upsert_events(normalize_events([_ev("sowal", "6:00 pm")]), run_id="r2", path=p)

    events = load_events(path=p)
    assert len(events) == 1                       # ONE canonical event, not two
    ev = events[0]
    assert ev["source_count"] == 2                # both sources attached
    assert ev["verification_count"] == 2          # and they agree
    assert ev["conflict_flag"] == 0
    assert len(load_event_observations(ev["id"], path=p)) == 2

    # corroboration raises confidence above either source alone
    single = normalize_events([_ev("sowal", "6:00 pm")])[0]["confidence"]
    assert ev["confidence"] > single


def test_conflicting_source_is_flagged_not_duplicated():
    p = _db()
    upsert_events(normalize_events([_ev("dashboard_legacy", "6:00 pm")]), run_id="r1", path=p)
    upsert_events(normalize_events([_ev("sowal", "8:00 pm")]), run_id="r2", path=p)

    events = load_events(path=p)
    assert len(events) == 1
    ev = events[0]
    assert ev["source_count"] == 2
    assert ev["conflict_flag"] == 1
    assert "Time mismatch" in ev["conflict_reason"]


def test_repeat_observation_same_source_does_not_duplicate():
    p = _db()
    upsert_events(normalize_events([_ev("sowal")]), run_id="r1", path=p)
    upsert_events(normalize_events([_ev("sowal")]), run_id="r2", path=p)  # same content again

    events = load_events(path=p)
    assert len(events) == 1
    assert len(load_event_observations(events[0]["id"], path=p)) == 1   # refreshed, not duplicated
    assert events[0]["source_count"] == 1


def test_upsert_reports_new_then_unchanged():
    p = _db()
    r1 = upsert_events(normalize_events([_ev("sowal")]), run_id="r1", path=p)
    assert len(r1["new"]) == 1

    r2 = upsert_events(normalize_events([_ev("sowal")]), run_id="r2", path=p)
    assert len(r2["new"]) == 0
    assert len(r2["changed"]) == 0
    assert len(r2["unchanged"]) == 1
