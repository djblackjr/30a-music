"""
tests/test_importer.py
Tests for the screenshot importer's pure logic (no OpenAI API calls):
confidence coercion, model-confidence passthrough, and date resolution.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.images.importer import _call_gpt4o, _coerce_confidence, _normalise
from app.normalize import normalize_events

IMG = Path("Shelbys_schedule.png")


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content, finish_reason):
        self.message = _FakeMessage(content)
        self.finish_reason = finish_reason


class _FakeResponse:
    def __init__(self, content="[]", finish_reason="stop"):
        self.choices = [_FakeChoice(content, finish_reason)]


class _FakeCompletions:
    def __init__(self, response, captured):
        self._response = response
        self._captured = captured

    def create(self, **kwargs):
        self._captured.update(kwargs)
        return self._response


class _FakeOpenAIClient:
    def __init__(self, response, captured, api_key=None):
        self.chat = type("_Chat", (), {"completions": _FakeCompletions(response, captured)})()


def _patch_openai(monkeypatch, response):
    """Stub out the `openai` package's client so _call_gpt4o makes no network call."""
    captured = {}
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    import openai
    monkeypatch.setattr(openai, "OpenAI", lambda api_key=None: _FakeOpenAIClient(response, captured))
    return captured


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


# --- _call_gpt4o request shape (no real network call) -----------------------
# Regression coverage for a real bug: max_tokens=2000 truncated GPT-4o's JSON
# response mid-object for a dense calendar-grid flyer (a full month at one
# venue can be 30+ events), which silently produced 0 events instead of an
# error. Confirmed live against a real image before raising the cap.

def test_call_gpt4o_requests_enough_max_tokens_for_dense_grids(monkeypatch, tmp_path):
    captured = _patch_openai(monkeypatch, _FakeResponse())
    img = tmp_path / "test.png"
    img.write_bytes(b"fake-image-bytes")

    _call_gpt4o(img)

    assert captured["max_tokens"] >= 4000


def test_call_gpt4o_warns_when_response_is_truncated(monkeypatch, tmp_path, caplog):
    _patch_openai(monkeypatch, _FakeResponse(finish_reason="length"))
    img = tmp_path / "test.png"
    img.write_bytes(b"fake-image-bytes")

    import logging
    with caplog.at_level(logging.WARNING):
        _call_gpt4o(img)

    assert "truncated" in caplog.text
