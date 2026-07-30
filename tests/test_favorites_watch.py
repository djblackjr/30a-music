"""
tests/test_favorites_watch.py
Tests for the pure logic in app.favorites_watch: parsing the research
model's JSON response, and filtering the main pipeline's new/changed
events down to favorites_watch's own findings for the push-notification
step. No network/API calls -- send_notification is monkeypatched.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.favorites_watch import pipeline_notify
from app.favorites_watch.research import _parse_response


def _event(performer, venue, date="2026-08-03", time_start="7PM", source="favorites_watch"):
    return {"performer": performer, "venue": venue, "date": date, "time_start": time_start, "source": source}


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


def test_notify_ignores_new_events_from_other_crawlers_even_at_a_favorite_venue(monkeypatch):
    # Confirmed live: filtering by venue/performer name alone (regardless of
    # source) produced a 165-event notification on its first real run, since
    # SoWal/AJ's Grayton/etc. surface dozens of ordinary bookings a day at
    # popular favorite venues. Only favorites_watch's own findings qualify.
    sent = _capture_notifications(monkeypatch)
    changes = {"new": [_event("Some Rando", "LaGrange Bayou", source="sowal")], "changed": []}
    assert pipeline_notify.notify_favorites_changes(changes) is False
    assert sent == []


def test_notify_fires_for_a_new_favorites_watch_finding(monkeypatch):
    sent = _capture_notifications(monkeypatch)
    changes = {"new": [_event("Stevie Monce", "LaGrange Bayou")], "changed": []}
    assert pipeline_notify.notify_favorites_changes(changes) is True
    assert len(sent) == 1
    assert "Stevie Monce" in sent[0][1] and "LaGrange Bayou" in sent[0][1]


def test_notify_reports_changed_events_using_the_after_state(monkeypatch):
    sent = _capture_notifications(monkeypatch)
    changes = {
        "new": [],
        "changed": [{"before": _event("X", "LaGrange Bayou", date="2026-08-03"),
                     "after": _event("X", "LaGrange Bayou", date="2026-08-10")}],
    }
    assert pipeline_notify.notify_favorites_changes(changes) is True
    assert "2026-08-10" in sent[0][1]


def test_notify_ignores_changed_events_from_other_crawlers(monkeypatch):
    sent = _capture_notifications(monkeypatch)
    changes = {
        "new": [],
        "changed": [{"before": _event("X", "LaGrange Bayou", date="2026-08-03", source="ajs_grayton"),
                     "after": _event("X", "LaGrange Bayou", date="2026-08-10", source="ajs_grayton")}],
    }
    assert pipeline_notify.notify_favorites_changes(changes) is False
    assert sent == []


def test_notify_truncates_a_long_list_instead_of_producing_an_oversized_message(monkeypatch):
    # ntfy.sh silently turns an oversized message into an unreadable file
    # attachment rather than a real push (confirmed live) -- MAX_LINES keeps
    # this from happening even if something upstream ever misbehaves again.
    sent = _capture_notifications(monkeypatch)
    many = [_event(f"Artist {i}", "LaGrange Bayou", date=f"2026-08-{i:02d}") for i in range(1, 31)]
    changes = {"new": many, "changed": []}
    assert pipeline_notify.notify_favorites_changes(changes) is True
    lines = sent[0][1].splitlines()
    assert len(lines) == pipeline_notify.MAX_LINES + 1
    assert "more" in lines[-1]
