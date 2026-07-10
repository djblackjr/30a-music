"""
tests/test_importer.py
Tests for the screenshot importer's pure logic (no OpenAI API calls):
confidence coercion, model-confidence passthrough, and date resolution.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.images.importer import _coerce_confidence, _normalise
from app.normalize import normalize_events

IMG = Path("Shelbys_schedule.png")


# --- confidence coercion ---------------------------------------------------

def test_coerce_confidence_valid():
    assert _coerce_confidence(0.9) == 0.9
    assert _coerce_confidence("0.5") == 0.5


def test_coerce_confidence_clamps():
    assert _coerce_confidence(1.5) == 1.0
    assert _coerce_confidence(-1) == 0.0


def test_coerce_confidence_invalid():
    assert _coerce_confidence("abc") is None
    assert _coerce_confidence(None) is None


# --- _normalise ------------------------------------------------------------

def test_normalise_passes_model_confidence():
    out = _normalise([{"artist": "Stevie Monce", "venue": "Chiringo", "date": "2026-07-10", "confidence": 0.9}], IMG)
    assert out[0]["model_confidence"] == 0.9
    assert out[0]["source"] == "image:Shelbys_schedule.png"


def test_normalise_drops_empty_artist():
    out = _normalise([{"artist": "", "venue": "Chiringo"}], IMG)
    assert out == []


def test_normalise_resolves_day_of_week_from_week_anchor():
    # week_of 7/6 (a Monday in 2026) + Wednesday -> 2026-07-08
    out = _normalise([{"artist": "X", "day_of_week": "Wednesday", "week_of": "7/6", "time_start": "6PM"}], IMG)
    assert out[0]["date"] == "2026-07-08"


def test_normalise_missing_confidence_is_none():
    out = _normalise([{"artist": "X", "venue": "V", "date": "2026-07-10"}], IMG)
    assert out[0]["model_confidence"] is None


# --- integration: importer output flows through the scorer -----------------

def test_model_confidence_feeds_extraction_confidence():
    ev = _normalise([{"artist": "X", "venue": "V", "date": "2026-07-10", "time_start": "6PM", "confidence": 1.0}], IMG)
    scored = normalize_events(ev)
    # image source_confidence 0.8, full fields + model 1.0 -> extraction 1.0
    # single observation -> event confidence = 0.8 * 1.0 = 0.8
    assert scored[0]["confidence"] == 0.8
    assert scored[0]["source_count"] == 1
    assert scored[0]["observations"][0]["extraction_confidence"] == 1.0
