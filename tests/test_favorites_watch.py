"""
tests/test_favorites_watch.py
Tests for the pure logic in app.favorites_watch: parsing the research
model's JSON response, and filtering the main pipeline's new/changed
events down to favorites for the push-notification step. No network/API
calls -- send_notification and the favorites CSVs are monkeypatched, same
pattern tests/test_dashboard.py already uses for _load_favorite_venues /
_load_performer_meta.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.favorites_watch import pipeline_notify
from app.favorites_watch.research import _parse_response


def _event(performer, venue, date="2026-08-03", time_start="7PM"):
    return {"performer": performer, "venue": venue, "date": date, "time_start": time_start}


def _patch_favorites(monkeypatch, venues=(), performers=()):
    monkeypatch.setattr(pipeline_notify, "_load_favorite_venues", lambda *a, **k: {v.lower() for v in venues})
    monkeypatch.setattr(pipeline_notify, "_load_performer_meta", lambda *a, **k: {p.lower(): True for p in performers})


def _capture_notifications(monkeypatch):
    sent = []
    monkeypatch.setattr(pipeline_notify, "send_notification", lambda title, message: sent.append((title, message)) or True)
    return sent


def test_parse_response_accepts_clean_json():
    raw = '{"found": true, "performer": "Stevie Monce", "venue": "Shades", "date": "2026-08-03", "time": "12-3PM", "source_url": "https://30a.com/x"}'
    result = _parse_response(raw)
    assert result["date"] == "2026-08-03"


def test_parse_response_strips_markdown_fences():
    raw = '```json\n{"found": true, "performer": "X", "venue": "Y", "date": "2026-08-03", "time": null, "source_url": "https://example.com"}\n```'
    result = _parse_response(raw)
    assert result is not None
    assert result["venue"] == "Y"


def test_parse_response_returns_none_when_not_found():
    raw = '{"found": false, "performer": null, "venue": null, "date": null, "time": null, "source_url": null}'
    assert _parse_response(raw) is None


def test_parse_response_drops_finding_missing_date_or_source():
    # A "found: true" result missing the date or source URL fails the Tier 1
    # bar just as much as a straightforward "not found" -- see research.py's
    # prompt, which requires both a real date and a real source URL.
    missing_date = '{"found": true, "performer": "X", "venue": "Y", "date": null, "time": "7PM", "source_url": "https://example.com"}'
    missing_source = '{"found": true, "performer": "X", "venue": "Y", "date": "2026-08-03", "time": "7PM", "source_url": null}'
    assert _parse_response(missing_date) is None
    assert _parse_response(missing_source) is None


def test_parse_response_drops_a_link_description_masquerading_as_a_source_url():
    # Confirmed live against the real model: it sometimes returns a prose
    # description of a link instead of an actual URL (e.g. "link to event on
    # 30a.com listing Wednesday, August 12 2026") despite the prompt asking
    # for a real http(s):// URL. A truthiness check alone let that through.
    raw = (
        '{"found": true, "performer": "X", "venue": "Y", "date": "2026-08-03", "time": "7PM", '
        '"source_url": "link to event on 30a.com \\"X @ Y\\" listing Wednesday, August 3 2026"}'
    )
    assert _parse_response(raw) is None


def test_parse_response_returns_none_on_garbage():
    assert _parse_response("not json at all") is None


def test_notify_skips_new_events_at_a_non_favorite_venue_by_a_non_favorite_performer(monkeypatch):
    _patch_favorites(monkeypatch, venues=["LaGrange Bayou"], performers=["Stevie Monce"])
    sent = _capture_notifications(monkeypatch)
    changes = {"new": [_event("Some Rando", "Some Random Bar")], "changed": []}
    assert pipeline_notify.notify_favorites_changes(changes) is False
    assert sent == []


def test_notify_fires_for_a_new_event_at_a_favorite_venue_even_with_a_non_favorite_performer(monkeypatch):
    _patch_favorites(monkeypatch, venues=["LaGrange Bayou"], performers=[])
    sent = _capture_notifications(monkeypatch)
    changes = {"new": [_event("Some Rando", "LaGrange Bayou")], "changed": []}
    assert pipeline_notify.notify_favorites_changes(changes) is True
    assert len(sent) == 1
    assert "LaGrange Bayou" in sent[0][1]


def test_notify_fires_for_a_favorite_performer_even_at_a_non_favorite_venue(monkeypatch):
    _patch_favorites(monkeypatch, venues=[], performers=["Stevie Monce"])
    sent = _capture_notifications(monkeypatch)
    changes = {"new": [_event("Stevie Monce", "Some Random Bar")], "changed": []}
    assert pipeline_notify.notify_favorites_changes(changes) is True
    assert "Stevie Monce" in sent[0][1]


def test_notify_reports_changed_events_using_the_after_state(monkeypatch):
    _patch_favorites(monkeypatch, venues=["LaGrange Bayou"], performers=[])
    sent = _capture_notifications(monkeypatch)
    changes = {
        "new": [],
        "changed": [{"before": _event("X", "LaGrange Bayou", date="2026-08-03"),
                     "after": _event("X", "LaGrange Bayou", date="2026-08-10")}],
    }
    assert pipeline_notify.notify_favorites_changes(changes) is True
    assert "2026-08-10" in sent[0][1]


def test_notify_ignores_changed_events_at_non_favorite_spots(monkeypatch):
    _patch_favorites(monkeypatch, venues=["LaGrange Bayou"], performers=[])
    sent = _capture_notifications(monkeypatch)
    changes = {
        "new": [],
        "changed": [{"before": _event("X", "Some Random Bar", date="2026-08-03"),
                     "after": _event("X", "Some Random Bar", date="2026-08-10")}],
    }
    assert pipeline_notify.notify_favorites_changes(changes) is False
    assert sent == []
