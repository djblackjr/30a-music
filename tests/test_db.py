"""
tests/test_db.py
Tests for app/database/db.py's maintenance functions: recompute_aggregates
and recanonicalize_venues. Uses a throwaway SQLite file per test (tmp_path),
never the real data/events.db.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database.db import init_db, recanonicalize_venues, recompute_aggregates, upsert_events
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
    row = conn.execute("SELECT time_start FROM events WHERE performer = 'Cade Pierce'").fetchone()
    conn.close()
    assert row[0] == "6:00 - 9:00 PM"


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
