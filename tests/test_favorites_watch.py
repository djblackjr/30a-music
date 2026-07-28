"""
tests/test_favorites_watch.py
Tests for the pure logic in app.favorites_watch: diffing findings between
runs and parsing the research model's JSON response. No network/API calls.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.favorites_watch.diff import diff_findings
from app.favorites_watch.research import _parse_response


def _finding(favorite_name, performer, venue, date, time="7PM", source_url="https://example.com"):
    return {
        "favorite_name": favorite_name,
        "found": True,
        "performer": performer,
        "venue": venue,
        "date": date,
        "time": time,
        "source_url": source_url,
    }


def test_diff_reports_brand_new_favorite_as_new():
    old = []
    new = [_finding("Stevie Monce", "Stevie Monce", "Shades Bar & Grill", "2026-08-03")]
    changes = diff_findings(old, new)
    assert len(changes["new"]) == 1
    assert changes["new"][0]["favorite_name"] == "Stevie Monce"
    assert changes["changed"] == []
    assert changes["removed"] == []


def test_diff_reports_same_favorite_different_date_as_changed_not_new_plus_removed():
    old = [_finding("Stevie Monce", "Stevie Monce", "Shades Bar & Grill", "2026-08-03")]
    new = [_finding("Stevie Monce", "Stevie Monce", "Shades Bar & Grill", "2026-08-10")]
    changes = diff_findings(old, new)
    assert changes["new"] == []
    assert changes["removed"] == []
    assert len(changes["changed"]) == 1
    assert changes["changed"][0]["before"]["date"] == "2026-08-03"
    assert changes["changed"][0]["after"]["date"] == "2026-08-10"


def test_diff_reports_favorite_no_longer_found_as_removed():
    old = [_finding("Stevie Monce", "Stevie Monce", "Shades Bar & Grill", "2026-08-03")]
    new = []
    changes = diff_findings(old, new)
    assert changes["new"] == []
    assert changes["changed"] == []
    assert len(changes["removed"]) == 1


def test_diff_is_a_noop_when_nothing_changed():
    finding = _finding("Stevie Monce", "Stevie Monce", "Shades Bar & Grill", "2026-08-03")
    changes = diff_findings([finding], [dict(finding)])
    assert changes == {"new": [], "changed": [], "removed": []}


def test_diff_is_keyed_by_favorite_name_not_by_full_finding_identity():
    # Same favorite, only the venue changed (e.g. a show moved locations) --
    # this must land in "changed", not show up as unrelated new+removed.
    old = [_finding("Stevie Monce", "Stevie Monce", "Shades Bar & Grill", "2026-08-03")]
    new = [_finding("Stevie Monce", "Stevie Monce", "Papa Surf", "2026-08-03")]
    changes = diff_findings(old, new)
    assert changes["new"] == [] and changes["removed"] == []
    assert changes["changed"][0]["after"]["venue"] == "Papa Surf"


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
