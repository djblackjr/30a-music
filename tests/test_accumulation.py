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

from app.database.db import (
    init_db,
    load_event_observations,
    load_events,
    purge_past_events,
    purge_source_observations,
    upsert_events,
)
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


# --- purging past events ----------------------------------------------------

def test_purge_past_events_deletes_only_dates_before_cutoff():
    p = _db()
    upsert_events(normalize_events([_ev("sowal", date="2026-07-10")]), run_id="r1", path=p)
    upsert_events(normalize_events([_ev("sowal", date="2026-07-15")]), run_id="r1", path=p)
    upsert_events(normalize_events([_ev("sowal", date="2026-07-20")]), run_id="r1", path=p)

    deleted = purge_past_events(before="2026-07-15", path=p)
    assert deleted == 1

    remaining = sorted(e["date"] for e in load_events(path=p))
    assert remaining == ["2026-07-15", "2026-07-20"]


def test_purge_past_events_also_drops_their_observations():
    p = _db()
    upsert_events(normalize_events([_ev("sowal", date="2026-07-10")]), run_id="r1", path=p)
    [ev] = load_events(path=p)
    assert load_event_observations(ev["id"], path=p)   # sanity: has an observation

    purge_past_events(before="2026-07-15", path=p)
    assert load_event_observations(ev["id"], path=p) == []


def test_purge_past_events_is_a_no_op_when_nothing_is_past():
    p = _db()
    upsert_events(normalize_events([_ev("sowal", date="2026-07-20")]), run_id="r1", path=p)
    assert purge_past_events(before="2026-07-15", path=p) == 0
    assert len(load_events(path=p)) == 1


# --- retiring a stale source -------------------------------------------------

def test_purge_source_drops_it_from_a_corroborated_event_without_deleting_the_event():
    p = _db()
    # same performer/venue/date from two sources -> one event, source_count=2
    upsert_events(normalize_events([_ev("sowal", performer="Jim Couch")]), run_id="r1", path=p)
    upsert_events(normalize_events([_ev("dashboard_legacy", performer="Jim Couch")]), run_id="r1", path=p)
    [ev] = load_events(path=p)
    assert ev["source_count"] == 2

    result = purge_source_observations("dashboard_legacy", path=p)
    assert result["observations_deleted"] == 1
    assert result["events_deleted"] == 0

    [ev] = load_events(path=p)
    assert ev["source_count"] == 1
    remaining_obs = load_event_observations(ev["id"], path=p)
    assert [o["source"] for o in remaining_obs] == ["sowal"]


def test_purge_source_deletes_an_event_that_only_existed_from_that_source():
    p = _db()
    upsert_events(normalize_events([_ev("dashboard_legacy", performer="Karaoke")]), run_id="r1", path=p)
    upsert_events(normalize_events([_ev("sowal", performer="Karaoke Night")]), run_id="r1", path=p)
    assert len(load_events(path=p)) == 2   # different performer strings -> two events

    result = purge_source_observations("dashboard_legacy", path=p)
    assert result["observations_deleted"] == 1
    assert result["events_deleted"] == 1

    remaining = load_events(path=p)
    assert len(remaining) == 1
    assert remaining[0]["performer"] == "Karaoke Night"


def test_purge_source_leaves_other_sources_untouched():
    p = _db()
    upsert_events(normalize_events([_ev("sowal", performer="Untouched")]), run_id="r1", path=p)
    purge_source_observations("dashboard_legacy", path=p)
    events = load_events(path=p)
    assert len(events) == 1
    assert events[0]["performer"] == "Untouched"


def test_purge_source_is_a_no_op_for_an_absent_source():
    p = _db()
    upsert_events(normalize_events([_ev("sowal")]), run_id="r1", path=p)
    result = purge_source_observations("does_not_exist", path=p)
    assert result == {"observations_deleted": 0, "events_deleted": 0, "events_recomputed": 0}
    assert len(load_events(path=p)) == 1
