"""
tests/test_normalize.py
Tests for the app/normalize package: canonicalisation, time normalisation,
venue default times, the single normalisation pass, and confidence scoring.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.normalize import normalize_events, ConfidenceAggregator
from app.normalize.canonical import canonicalize
from app.normalize.times import apply_venue_default_time, normalize_time
from app.normalize.confidence import (
    confidence_band,
    extraction_confidence,
    observation_confidence,
    score_event,
    source_confidence,
)


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


def test_normalize_different_times_same_identity_is_one_event_with_conflict():
    # Same performer+venue+date, different times -> ONE event, time conflict.
    out = normalize_events([
        _raw("Artist A", "Venue X", time_start="6PM", source="sowal"),
        _raw("Artist A", "Venue X", time_start="9PM", source="crawler"),
    ])
    assert len(out) == 1
    assert out[0]["conflict_flag"] == 1
    assert "Time mismatch" in out[0]["conflict_reason"]


def test_normalize_attaches_confidence():
    out = normalize_events([_raw("Artist A", "Venue X", source="seed")])
    assert 0.0 <= out[0]["confidence"] <= 1.0
    assert out[0]["confidence_reason"]


def test_observation_type_inferred_from_source():
    def otype(src):
        return normalize_events([_raw("A", "V", source=src)])[0]["observations"][0]["observation_type"]
    assert otype("sowal") == "website"
    assert otype("image:flyer.png") == "image"
    assert otype("seed") == "manual"
    assert otype("instagram") == "social"


def test_observation_type_explicit_wins():
    out = normalize_events([_raw("A", "V", source="bandsintown", observation_type="api")])
    assert out[0]["observations"][0]["observation_type"] == "api"


def test_normalize_single_source_provenance():
    out = normalize_events([_raw("Artist A", "Venue X", source="sowal")])
    ev = out[0]
    assert ev["source_count"] == 1
    assert ev["verification_count"] == 1
    assert ev["conflict_flag"] == 0
    assert len(ev["observations"]) == 1


def test_normalize_corroborating_sources_boost_confidence():
    single = normalize_events([_raw("Artist A", "Venue X", source="sowal")])[0]
    both = normalize_events([
        _raw("Artist A", "Venue X", source="sowal"),
        _raw("Artist A", "Venue X", source="venue"),
    ])[0]
    assert both["source_count"] == 2
    assert both["verification_count"] == 2
    assert both["conflict_flag"] == 0
    assert both["confidence"] > single["confidence"]
    assert len(both["observations"]) == 2


def test_normalize_canonicalises_in_pass():
    out = normalize_events([_raw("STEVIE MONCE", "Venue X")])
    assert out[0]["performer"] == "Stevie Monce"


# --- confidence: two dimensions + effective score --------------------------

def test_source_confidence_by_source():
    assert source_confidence("sowal") == 0.90
    assert source_confidence("seed") == 0.60
    assert source_confidence("image:flyer.png") == 0.80
    assert source_confidence("unknown-thing") == 0.50


def test_extraction_confidence_rises_with_completeness():
    full = extraction_confidence(_raw("A", "V", date="2026-07-04", time_start="6PM"))
    sparse = extraction_confidence({"performer": "A"})
    assert full > sparse


def test_extraction_confidence_blends_model_when_headroom():
    sparse = {"performer": "A", "venue": "V"}  # no date/time -> completeness < 1
    with_model = extraction_confidence({**sparse, "model_confidence": 1.0})
    without = extraction_confidence(sparse)
    assert with_model > without


def test_observation_confidence_is_product():
    ev = _raw("A", "V", source="sowal", date="2026-07-04", time_start="6PM")
    assert observation_confidence(ev) == round(
        source_confidence("sowal") * extraction_confidence(ev), 3)


def test_score_event_bounds_and_reason():
    score, reason = score_event(_raw("A", "V", source="image:flyer.png"))
    assert 0.0 <= score <= 1.0
    assert "source=" in reason


def test_confidence_bands():
    assert confidence_band(0.9) == "high"
    assert confidence_band(0.6) == "medium"
    assert confidence_band(0.2) == "low"
    assert confidence_band(None) == "unknown"


# --- ConfidenceAggregator (hybrid model) -----------------------------------

def _obs(source, confidence):
    return {"source": source, "confidence": confidence}


def test_aggregator_single_source():
    agg = ConfidenceAggregator()
    assert agg.aggregate([_obs("venue", 0.96)]) == 0.96


def test_aggregator_two_agreeing_sources_boost():
    agg = ConfidenceAggregator()
    score = agg.aggregate([_obs("venue", 0.96), _obs("sowal", 0.90)])
    assert 0.96 < score <= 0.99  # corroboration raises above the best single


def test_aggregator_three_agreeing_near_ceiling():
    agg = ConfidenceAggregator()
    score = agg.aggregate([_obs("venue", 0.96), _obs("artist", 0.9), _obs("sowal", 0.9)])
    assert score >= 0.98
    assert score <= 0.99


def test_aggregator_caps_at_ceiling():
    agg = ConfidenceAggregator()
    score = agg.aggregate([_obs(f"s{i}", 0.99) for i in range(10)])
    assert score <= 0.99


def test_aggregator_independence_same_source_no_double_count():
    agg = ConfidenceAggregator()
    # two sightings from the SAME source must not corroborate
    assert agg.aggregate([_obs("venue", 0.96), _obs("venue", 0.96)]) == 0.96


def test_aggregator_conflict_reduces_confidence():
    agg = ConfidenceAggregator()
    clean = agg.aggregate([_obs("venue", 0.96)], has_conflict=False)
    conflicted = agg.aggregate([_obs("venue", 0.96)], has_conflict=True)
    assert conflicted < clean


def test_aggregator_low_quality_agreeing_never_reduces():
    agg = ConfidenceAggregator()
    base = agg.aggregate([_obs("venue", 0.96)])
    plus_weak = agg.aggregate([_obs("venue", 0.96), _obs("weak", 0.30)])
    assert plus_weak >= base
