"""
tests/test_normalize.py
Tests for the app/normalize package: canonicalisation, time normalisation,
venue default times, the single normalisation pass, and confidence scoring.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.normalize import normalize_events
from app.normalize.canonical import canonicalize
from app.normalize.times import apply_venue_default_time, normalize_time
from app.normalize.confidence import confidence_band, score_event


# --- canonicalisation ------------------------------------------------------

def test_canonicalize_known_variant():
    assert canonicalize("STEVIE MONCE") == "Stevie Monce"
    assert canonicalize("Casey Kearney Band") == "Casey Kearney"


def test_canonicalize_unknown_passthrough():
    assert canonicalize("Some New Artist") == "Some New Artist"


def test_canonicalize_none_and_empty():
    assert canonicalize(None) is None
    assert canonicalize("") == ""


# --- time normalisation ----------------------------------------------------

def test_normalize_time_range_24h_to_12h():
    assert normalize_time("18:00 - 21:00") == "6:00 PM - 9:00 PM"


def test_normalize_time_single_24h():
    assert normalize_time("14:30") == "2:30 PM"


def test_normalize_time_keeps_ampm():
    assert normalize_time("6PM") == "6PM"


def test_venue_default_time_applied_when_missing():
    assert apply_venue_default_time("Shelby's Beach Bar", "") == "6:00 - 9:00 PM"
    assert apply_venue_default_time("Papa Surf", "UNKNOWN") == "6:00 - 9:00 PM"


def test_venue_default_time_does_not_overwrite_real_time():
    assert apply_venue_default_time("Papa Surf", "8PM") == "8PM"


# --- normalisation pass ----------------------------------------------------

def _raw(performer, venue=None, date="2026-07-04", time_start="6PM", **kw):
    return {"performer": performer, "venue": venue, "date": date, "time_start": time_start, **kw}


def test_normalize_drops_empty_performer():
    assert normalize_events([_raw("")]) == []


def test_normalize_fills_name():
    out = normalize_events([_raw("Artist B", "Venue Y")])
    assert out[0]["name"] == "Artist B at Venue Y"


def test_normalize_dedup_exact_duplicates():
    out = normalize_events([_raw("Artist A", "Venue X"), _raw("Artist A", "Venue X")])
    assert len(out) == 1


def test_normalize_keeps_different_times():
    out = normalize_events([
        _raw("Artist A", "Venue X", time_start="6PM"),
        _raw("Artist A", "Venue X", time_start="9PM"),
    ])
    assert len(out) == 2


def test_normalize_attaches_confidence():
    out = normalize_events([_raw("Artist A", "Venue X", source="seed")])
    assert 0.0 <= out[0]["confidence"] <= 1.0
    assert out[0]["confidence_reason"]


def test_normalize_canonicalises_in_pass():
    out = normalize_events([_raw("STEVIE MONCE", "Venue X")])
    assert out[0]["performer"] == "Stevie Monce"


# --- confidence scoring ----------------------------------------------------

def test_score_complete_crawler_beats_sparse_seed():
    full, _ = score_event(_raw("A", "V", source="crawler"))
    sparse, _ = score_event({"performer": "A", "source": "seed"})
    assert full > sparse


def test_score_bounds_and_reason():
    score, reason = score_event(_raw("A", "V", source="image:flyer.png"))
    assert 0.0 <= score <= 1.0
    assert "source=" in reason


def test_score_blends_model_confidence():
    with_model, reason = score_event(_raw("A", "V", source="seed", model_confidence=1.0))
    without, _ = score_event(_raw("A", "V", source="seed"))
    assert with_model > without
    assert "model" in reason


def test_confidence_bands():
    assert confidence_band(0.9) == "high"
    assert confidence_band(0.6) == "medium"
    assert confidence_band(0.2) == "low"
    assert confidence_band(None) == "unknown"
