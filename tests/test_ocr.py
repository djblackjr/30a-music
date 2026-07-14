"""
tests/test_ocr.py
Apple Vision OCR importer — the pure parsing logic (no Vision framework needed,
so these run on Linux/CI too) plus the importer-selection rules.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.images import ingest_inbox
from app.images.ocr import match_venue, parse_two_column
from app.normalize import normalize_events


# --- venue matching ---------------------------------------------------------

def test_match_venue_keywords():
    assert match_venue("STINKY'S BAIT SHACK") == "Stinky's Bait Shack"
    assert match_venue("aj's grayton beach") == "AJ's Grayton"
    assert match_venue("RED FISH") == "Red Fish Taco"
    assert match_venue("Something Unknown") is None


# --- two-column positional parsing ------------------------------------------

def _item(x, y, text):
    return {"x": x, "y": y, "text": text}


def test_parse_two_column_builds_event():
    # a left-column sequence: date -> venue -> time  (y descends down the image)
    items = [
        _item(0.1, 0.9, "FRI JUL 17"),
        _item(0.1, 0.8, "STINKY'S BAIT SHACK"),
        _item(0.1, 0.7, "7:00 PM"),
    ]
    events = parse_two_column(items, artist="Stevie Monce", year=2026)
    assert len(events) == 1
    ev = events[0]
    assert ev["performer"] == "Stevie Monce"
    assert ev["venue"] == "Stinky's Bait Shack"
    assert ev["date"] == "2026-07-17"
    assert ev["time_start"] == "7:00 PM"


def test_parse_two_column_reads_both_columns():
    items = [
        _item(0.1, 0.9, "FRI JUL 17"), _item(0.1, 0.8, "CHIRINGO"), _item(0.1, 0.7, "6:00 PM"),
        _item(0.8, 0.9, "SAT JUL 18"), _item(0.8, 0.8, "PAPA SURF"), _item(0.8, 0.7, "8:00 PM"),
    ]
    events = parse_two_column(items, artist="A", year=2026)
    assert len(events) == 2
    assert {e["venue"] for e in events} == {"Chiringo", "Papa Surf"}


def test_parse_two_column_needs_date_and_venue():
    # a time with no preceding venue produces nothing
    items = [_item(0.1, 0.9, "FRI JUL 17"), _item(0.1, 0.7, "7:00 PM")]
    assert parse_two_column(items, artist="A", year=2026) == []


# --- OCR events flow through the pipeline with the right provenance ----------

def test_ocr_events_normalise_with_ocr_provenance():
    raw = parse_two_column(
        [{"x": 0.1, "y": 0.9, "text": "FRI JUL 17"},
         {"x": 0.1, "y": 0.8, "text": "CHIRINGO"},
         {"x": 0.1, "y": 0.7, "text": "6:00 PM"}],
        artist="Stevie Monce", year=2026,
    )
    for ev in raw:
        ev["source"] = "ocr:schedule.png"
        ev["observation_type"] = "ocr"

    out = normalize_events(raw)
    obs = out[0]["observations"][0]
    assert obs["observation_type"] == "ocr"
    # OCR is the least-trusted source (0.5), so confidence stays modest
    assert obs["source_confidence"] == 0.5
    assert out[0]["confidence"] < 0.8


# --- importer selection -----------------------------------------------------

def test_ingest_inbox_returns_empty_when_inbox_empty(monkeypatch):
    # no key -> OCR path; empty inbox -> no events, nothing blows up
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert ingest_inbox() == []
