"""
tests/test_sowal.py
Tests for the SoWal crawler's pure parsing helpers, its registration, that its
output normalises correctly, and that it reconciles under the identity model.
No network calls.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.crawlers.sowal import parse_time, parse_when, split_title
from app.crawlers.registry import ALL_CRAWLERS
from app.normalize import normalize_events
from app.reconcile.changes import compare_runs


# --- parsing helpers -------------------------------------------------------

def test_split_title_performer_and_venue():
    assert split_title("Duncan Crittenden @ Local Catch Bar & Grill") == (
        "Duncan Crittenden", "Local Catch Bar & Grill")


def test_split_title_no_venue():
    assert split_title("Open Mic Night") == ("Open Mic Night", None)


def test_parse_when_full_format():
    assert parse_when("Saturday, July 11, 2026") == "2026-07-11"


def test_parse_when_unparseable():
    assert parse_when("this weekend") is None
    assert parse_when(None) is None


def test_parse_time_range():
    text = "When:\nSaturday, July 11, 2026\nTime:\n5:00 pm\nto\n8:00 pm\nWhere:\nLocal Catch"
    assert parse_time(text) == ("5:00 pm", "8:00 pm")


def test_parse_time_single():
    text = "Time:\n7:00 pm\nWhere:\nSomewhere"
    assert parse_time(text) == ("7:00 pm", None)


# --- registration ----------------------------------------------------------

def test_sowal_is_registered():
    names = [c.name for c in ALL_CRAWLERS]
    assert "sowal" in names


# --- normalisation of SoWal output -----------------------------------------

def _sowal_event(performer, venue, date, time_start="5:00 pm"):
    return {
        "name": f"{performer} @ {venue}",
        "performer": performer,
        "venue": venue,
        "date": date,
        "time_start": time_start,
        "time_end": None,
        "url": "https://sowal.com/event/x",
        "stage": None,
        "source": "sowal",
    }


def test_sowal_output_normalises_and_scores():
    out = normalize_events([_sowal_event("Duncan Crittenden", "Local Catch Bar & Grill", "2026-07-11")])
    ev = out[0]
    assert ev["source"] == "sowal"
    assert 0.0 <= ev["confidence"] <= 1.0
    # sowal base trust 0.9, all fields present -> 0.9
    assert ev["confidence"] == 0.9


# --- reconciliation under the identity model --------------------------------

def test_sowal_reconciliation_stable_across_identical_runs():
    run = normalize_events([_sowal_event("A", "V", "2026-07-11")])
    result = compare_runs(run, run)
    assert result["summary"]["unchanged"] == 1
    assert result["summary"]["total_delta"] == 0


def test_sowal_time_change_is_changed():
    prev = normalize_events([_sowal_event("A", "V", "2026-07-11", time_start="5:00 pm")])
    curr = normalize_events([_sowal_event("A", "V", "2026-07-11", time_start="7:00 pm")])
    result = compare_runs(curr, prev)
    assert result["summary"]["changed"] == 1
    assert result["summary"]["new"] == 0
    assert result["summary"]["removed"] == 0
