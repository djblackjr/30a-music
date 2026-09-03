"""
tests/test_db.py
Tests for app/database/db.py's maintenance functions: recompute_aggregates
and recanonicalize_venues. Uses a throwaway SQLite file per test (tmp_path),
never the real data/events.db.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database.db import (
    collapse_same_slot_duplicates_in_db,
    detect_schedule_conflicts,
    init_db,
    purge_recurring_series_placeholder_events,
    recanonicalize_venues,
    recompute_aggregates,
    upsert_events,
)
from app.normalize import normalize_events


def _raw(performer, venue, date="2026-07-16", time_start="6PM", source="sowal", **kw):
    return {"performer": performer, "venue": venue, "date": date, "time_start": time_start,
            "source": source, **kw}


def test_recompute_aggregates_keeps_gap_filled_time(tmp_path):
    # Regression: recompute_aggregates() used to read the primary observation's
    # raw time_start directly, bypassing the gap-filling aggregate_observations()
    # already computes -- so a merge that should have surfaced a corroborating
    # source's time_start could instead show None even though upsert_events()
    # itself would have filled it correctly on first insert.
    db = tmp_path / "test.db"
    init_db(db)

    events = normalize_events([
        _raw("Cade Pierce", "Papa Surf", time_start=None, source="sowal"),
        _raw("Cade Pierce", "Papa Surf", time_start="6:00 - 9:00 PM", source="image:flyer.png"),
    ])
    upsert_events(events, run_id="R1", path=db)

    updated = recompute_aggregates(db)
    assert updated == 1

    import sqlite3
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT time_start, time_end FROM events WHERE performer = 'Cade Pierce'"
    ).fetchone()
    conn.close()
    # split_time_range() at ingest (added 2026-08-08) splits an in-band range
    # into separate start/end fields rather than keeping it crammed into
    # time_start -- the gap-filled value should carry through split just
    # like a directly-ingested one would.
    assert row[0] == "6:00 PM"
    assert row[1] == "9:00 PM"


def test_recanonicalize_venues_merges_and_gap_fills(tmp_path, monkeypatch):
    # The real bug this session: the exact same real venue ("Papa Surf") was
    # saved under two different spellings by two different runs, so they
    # never merged -- one card with a time, a duplicate "papasurfburgerbar"
    # card with none. Adding a canonical alias + running this tool should
    # collapse them into one event AND surface the time the other had.
    import app.normalize.canonical as canonical_module
    monkeypatch.setitem(
        canonical_module._VARIANT_TO_CANONICAL, "papasurfburgerbar", "Papa Surf"
    )

    db = tmp_path / "test.db"
    init_db(db)

    # Simulate two separate runs (canonicalize() wasn't applied retroactively,
    # matching how the real duplicates were produced).
    ev1 = {
        "performer": "Cade Pierce", "venue": "Papa Surf", "date": "2026-07-16",
        "time_start": "6:00 - 9:00 PM", "source": "image:flyer.png",
        "observation_type": "image", "name": "Cade Pierce at Papa Surf",
        "confidence": 0.8, "source_count": 1, "verification_count": 1,
        "conflict_flag": 0, "conflict_reason": None,
        "observations": [{"source": "image:flyer.png", "observation_type": "image",
                           "time_start": "6:00 - 9:00 PM", "confidence": 0.8, "checksum": "a"}],
    }
    ev2 = {
        "performer": "Cade Pierce", "venue": "papasurfburgerbar", "date": "2026-07-16",
        "time_start": None, "source": "image:flyer.png",
        "observation_type": "image", "name": "Cade Pierce at papasurfburgerbar",
        "confidence": 0.6, "source_count": 1, "verification_count": 1,
        "conflict_flag": 0, "conflict_reason": None,
        "observations": [{"source": "image:flyer.png", "observation_type": "image",
                           "time_start": None, "confidence": 0.6, "checksum": "b"}],
    }
    upsert_events([ev1], run_id="R1", path=db)
    upsert_events([ev2], run_id="R2", path=db)

    import sqlite3
    conn = sqlite3.connect(db)
    before = conn.execute("SELECT COUNT(*) FROM events WHERE performer = 'Cade Pierce'").fetchone()[0]
    conn.close()
    assert before == 2

    result = recanonicalize_venues(db)
    assert result["merged"] == 1

    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT venue, time_start FROM events WHERE performer = 'Cade Pierce'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] == "Papa Surf"
    assert rows[0][1] == "6:00 - 9:00 PM"


def test_collapse_same_slot_duplicates_in_db_merges_pre_existing_rows(tmp_path):
    # Regression: app.normalize.provenance.collapse_same_slot_duplicates()
    # only merges venue-text variants seen together in ONE run's raw batch
    # -- it can't retroactively merge rows already stored separately from
    # earlier runs. Simulate that: three separate upsert_events() calls
    # (one per "run"), each with different venue text, so three genuinely
    # separate rows accumulate exactly like production did (confirmed live
    # 2026-09-03: three rows from a 2026-09-01 run, still separate two days
    # after the in-batch fix shipped since nothing re-observed all three
    # together in one batch since).
    db = tmp_path / "test.db"
    init_db(db)
    upsert_events(normalize_events([_raw("The Typos", "Venue A", source="sowal")]), run_id="R1", path=db)
    upsert_events(normalize_events([_raw("The Typos", "Venue B", source="venue_site")]), run_id="R2", path=db)
    upsert_events(normalize_events([_raw("The Typos", "Venue C", source="image:flyer.png")]), run_id="R3", path=db)

    import sqlite3
    conn = sqlite3.connect(db)
    before = conn.execute("SELECT COUNT(*) FROM events WHERE performer = 'The Typos'").fetchone()[0]
    conn.close()
    assert before == 3

    result = collapse_same_slot_duplicates_in_db(db)
    assert result == {"groups_found": 1, "events_merged": 2}

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM events WHERE performer = 'The Typos'").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0]["venue"] == "Venue C"  # flyer-backed variant wins
    assert rows[0]["source_count"] == 3


def test_collapse_same_slot_duplicates_in_db_breaks_flyer_tie_by_recency(tmp_path):
    # Regression: on a flyer-confidence TIE between two variants, the more
    # recently observed one must win, not lowest id / first-seen. Confirmed
    # live on the real "The Typos" 2026-09-10 booking: two flyer
    # observations tied at 0.80 confidence, and a first-seen tiebreak would
    # have kept the wrong (earlier) venue -- the opposite of what a manual
    # investigation of that exact booking (commit 9c23041) determined.
    db = tmp_path / "test.db"
    init_db(db)
    upsert_events(normalize_events([_raw("The Typos", "Venue A", source="image:a.png")]), run_id="R1", path=db)
    upsert_events(normalize_events([_raw("The Typos", "Venue B", source="image:b.png")]), run_id="R2", path=db)

    import sqlite3
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    venue_to_id = {r["venue"]: r["id"] for r in conn.execute(
        "SELECT id, venue FROM events WHERE performer = 'The Typos'"
    ).fetchall()}
    # Force an exact confidence tie; Venue B was observed later.
    conn.execute(
        "UPDATE event_observations SET confidence = 0.8, observed_at = '2026-09-01T10:00:00' WHERE event_id = ?",
        (venue_to_id["Venue A"],),
    )
    conn.execute(
        "UPDATE event_observations SET confidence = 0.8, observed_at = '2026-09-01T20:00:00' WHERE event_id = ?",
        (venue_to_id["Venue B"],),
    )
    conn.commit()
    conn.close()

    result = collapse_same_slot_duplicates_in_db(db)
    assert result == {"groups_found": 1, "events_merged": 1}

    conn = sqlite3.connect(db)
    row = conn.execute("SELECT venue FROM events WHERE performer = 'The Typos'").fetchone()
    conn.close()
    assert row[0] == "Venue B"


def test_collapse_same_slot_duplicates_in_db_is_safe_to_rerun(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    upsert_events(normalize_events([_raw("The Typos", "Venue A", source="sowal")]), run_id="R1", path=db)
    upsert_events(normalize_events([_raw("The Typos", "Venue B", source="venue_site")]), run_id="R2", path=db)

    first = collapse_same_slot_duplicates_in_db(db)
    assert first == {"groups_found": 1, "events_merged": 1}

    second = collapse_same_slot_duplicates_in_db(db)
    assert second == {"groups_found": 0, "events_merged": 0}


def test_collapse_same_slot_duplicates_in_db_leaves_different_times_alone(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    upsert_events(normalize_events([_raw("The Typos", "Venue A", time_start="6PM", source="sowal")]), run_id="R1", path=db)
    upsert_events(normalize_events([_raw("The Typos", "Venue B", time_start="9PM", source="venue_site")]), run_id="R2", path=db)

    result = collapse_same_slot_duplicates_in_db(db)
    assert result == {"groups_found": 0, "events_merged": 0}

    import sqlite3
    conn = sqlite3.connect(db)
    count = conn.execute("SELECT COUNT(*) FROM events WHERE performer = 'The Typos'").fetchone()[0]
    conn.close()
    assert count == 2


def test_purge_recurring_series_placeholder_events_removes_known_titles(tmp_path):
    # Regression: adding a title to RECURRING_SERIES_TITLES only stops NEW
    # crawls from saving it as a fake performer -- rows a past crawl already
    # saved stay put until something retroactively purges them. Confirmed
    # live 2026-09-03: "Watersound First Friday Concert Series" sat on the
    # dashboard as a fake performer for four dates.
    db = tmp_path / "test.db"
    init_db(db)
    upsert_events(normalize_events([
        _raw("Watersound First Friday Concert Series", None, date="2026-09-04"),
        _raw("The Pink Stones", "Watersound Town Center", date="2026-09-04"),
    ]), run_id="R1", path=db)

    import sqlite3
    conn = sqlite3.connect(db)
    before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.close()
    assert before == 2

    purged = purge_recurring_series_placeholder_events(db)
    assert purged == 1

    conn = sqlite3.connect(db)
    rows = [r[0] for r in conn.execute("SELECT performer FROM events").fetchall()]
    conn.close()
    assert rows == ["The Pink Stones"]


def test_purge_recurring_series_placeholder_events_is_safe_to_rerun(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    upsert_events(normalize_events([
        _raw("Sounds of Seaside at Seaside Amphitheater", None, date="2026-09-09"),
    ]), run_id="R1", path=db)

    assert purge_recurring_series_placeholder_events(db) == 1
    assert purge_recurring_series_placeholder_events(db) == 0


def test_detect_schedule_conflicts_flags_same_night_collision(tmp_path):
    # Two different performers at the same venue on the same night -- the
    # straightforward double-booking signal.
    db = tmp_path / "test.db"
    init_db(db)
    events = normalize_events([
        _raw("Harrison Prentice", "Red Fish Taco", date="2026-08-27"),
        _raw("Reid Fisher", "Red Fish Taco", date="2026-08-27"),
    ])
    upsert_events(events, run_id="R1", path=db)

    findings = detect_schedule_conflicts(db)
    collisions = [f for f in findings if f["type"] == "same_night_collision"]
    assert len(collisions) == 1
    assert collisions[0]["date"] == "2026-08-27"
    assert set(collisions[0]["performers"]) == {"Harrison Prentice", "Reid Fisher"}


def test_detect_schedule_conflicts_ignores_simultaneous_different_stages(tmp_path):
    # Regression: confirmed live 2026-08-03 -- AJ's Grayton Beach's own
    # multi-stage weekly flyer (Main Stage / Courtyard Stage / Round Room)
    # legitimately has two different acts at the same time on different
    # stages (e.g. Take 12 on Main Stage and DJ Babs in the Round Room, both
    # 9PM). That's not a double-booking and must not be flagged.
    db = tmp_path / "test.db"
    init_db(db)
    events = normalize_events([
        _raw("Take 12", "AJ's Grayton Beach", date="2026-08-07", time_start="9PM", stage="Main Stage"),
        _raw("DJ Babs", "AJ's Grayton Beach", date="2026-08-07", time_start="9PM", stage="Round Room"),
    ])
    upsert_events(events, run_id="R1", path=db)

    assert detect_schedule_conflicts(db) == []


def test_detect_schedule_conflicts_flags_irregular_recurrence(tmp_path):
    # Regression: the exact bug found live 2026-08-02 -- the same flyer
    # misread by GPT-4o Vision across two runs landed "Brett Stafford" on
    # both Sunday 8/9 and Monday 8/10 for the same venue, a day apart,
    # instead of merging into a single Monday booking.
    db = tmp_path / "test.db"
    init_db(db)
    events = normalize_events([
        _raw("Brett Stafford", "Red Fish Taco", date="2026-08-03"),  # Mon
        _raw("Brett Stafford", "Red Fish Taco", date="2026-08-09"),  # Sun (bad run)
        _raw("Brett Stafford", "Red Fish Taco", date="2026-08-10"),  # Mon (good run)
    ])
    upsert_events(events, run_id="R1", path=db)

    findings = detect_schedule_conflicts(db)
    irregular = [f for f in findings if f["type"] == "irregular_recurrence"]
    assert len(irregular) == 1
    assert irregular[0]["performer"] == "Brett Stafford"
    assert "2026-08-09" in irregular[0]["dates"]


def test_detect_schedule_conflicts_ignores_clean_weekly_residency(tmp_path):
    # A performer playing the same venue on the same weekday every week for
    # months is completely normal and must not be flagged.
    db = tmp_path / "test.db"
    init_db(db)
    events = normalize_events([
        _raw("Dion Jones", "Stinky's Bait Shack", date="2026-08-01"),  # Sat
        _raw("Dion Jones", "Stinky's Bait Shack", date="2026-08-08"),  # Sat
        _raw("Dion Jones", "Stinky's Bait Shack", date="2026-08-15"),  # Sat
    ])
    upsert_events(events, run_id="R1", path=db)

    assert detect_schedule_conflicts(db) == []


def test_detect_schedule_conflicts_ignores_distant_different_weekday_bookings(tmp_path):
    # A touring act legitimately playing the same venue on a different day
    # of the week weeks apart isn't a misread -- only bookings close enough
    # together (<=10 days) to plausibly be the same show read twice.
    db = tmp_path / "test.db"
    init_db(db)
    events = normalize_events([
        _raw("Casey Kearney", "Red Fish Taco", date="2026-08-04"),  # Tue
        _raw("Casey Kearney", "Red Fish Taco", date="2026-09-19"),  # Sat, 46 days later
    ])
    upsert_events(events, run_id="R1", path=db)

    assert detect_schedule_conflicts(db) == []
