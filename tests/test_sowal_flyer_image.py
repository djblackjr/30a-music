"""
tests/test_sowal_flyer_image.py
Tests for the flyer-image fallback (app/crawlers/sowal.py::SoWalCrawler._parse_flyer_image).

Some SoWal event pages publish their whole lineup as a single JPG poster with
no surrounding text at all (e.g. Crackings' "JULY LIVE MUSIC" flyer) -- title
and description extraction find nothing to work with, so this path off-loads
to the same GPT-4o Vision importer used for manually-dropped screenshots
(app/images/importer.py). Network and the OpenAI client are stubbed
throughout; nothing here makes a real HTTP or API call.
"""
import sys
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.crawlers.sowal import SoWalCrawler

_FLYER_HTML = """
<html><body>
  <h1>Live Music @ Crackings</h1>
  <img class="image-_bohr-body-image-full-width margin-bottom-standard" src="/sites/default/files/2_116.jpg?itok=abc" />
</body></html>
"""


# --- _find_flyer_image_url (pure, offline) ----------------------------------

def test_find_flyer_image_url_absolutizes_relative_src():
    soup = BeautifulSoup(_FLYER_HTML, "lxml")
    url = SoWalCrawler._find_flyer_image_url(soup)
    assert url == "https://sowal.com/sites/default/files/2_116.jpg?itok=abc"


def test_find_flyer_image_url_none_when_absent():
    soup = BeautifulSoup("<html><body><h1>X</h1></body></html>", "lxml")
    assert SoWalCrawler._find_flyer_image_url(soup) is None


def test_parse_flyer_image_returns_empty_without_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    soup = BeautifulSoup(_FLYER_HTML, "lxml")
    obs = SoWalCrawler()._parse_flyer_image(
        soup, "Crackings", "https://sowal.com/event/x", "Live Music @ Crackings"
    )
    assert obs == []


# --- full path with network + Vision stubbed --------------------------------

class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)
        self.finish_reason = "stop"


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, response):
        self._response = response

    def create(self, **kwargs):
        return self._response


class _FakeOpenAIClient:
    def __init__(self, response, api_key=None):
        self.chat = type("_Chat", (), {"completions": _FakeCompletions(response)})()


class _FakeImageResponse:
    """Stub for requests.get(img_url) -- fake JPEG bytes."""
    status_code = 200
    content = b"fake-jpeg-bytes"
    text = ""

    def raise_for_status(self):
        pass


_VISION_JSON = (
    '[{"artist": "Laura Lane", "venue": null, "date": "2026-07-15", '
    '"time_start": "9:30 am", "time_end": "12:30 pm", "confidence": 0.9}]'
)


def _patch_vision(monkeypatch, vision_json=_VISION_JSON):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    import openai
    monkeypatch.setattr(openai, "OpenAI", lambda api_key=None: _FakeOpenAIClient(_FakeResponse(vision_json)))


def test_parse_flyer_image_returns_named_observation(monkeypatch):
    _patch_vision(monkeypatch)
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeImageResponse())

    soup = BeautifulSoup(_FLYER_HTML, "lxml")
    obs = SoWalCrawler()._parse_flyer_image(
        soup, "Crackings. - Grayton Beach",
        "https://sowal.com/event/live-music-crackings-541", "Live Music @ Crackings",
    )

    assert len(obs) == 1
    assert obs[0]["performer"] == "Laura Lane"
    assert obs[0]["venue"] == "Crackings. - Grayton Beach"  # page venue wins over a missing flyer venue
    assert obs[0]["date"] == "2026-07-15"
    assert obs[0]["performer_status"] == "named"
    assert obs[0]["resolved"] is True
    assert obs[0]["extraction_method"] == "flyer_image"


def test_parse_flyer_image_fills_missing_time_from_page_fallback(monkeypatch):
    # Regression: 15 of 31 rows on the real 30Avenue July flyer came back
    # from Vision with no time at all, despite the page's own "Time: 6:00 pm
    # to 9:00 pm" field applying to the whole series -- confirmed live.
    _patch_vision(monkeypatch, vision_json=(
        '[{"artist": "Laura Lane", "venue": null, "date": "2026-07-15", '
        '"confidence": 0.9}]'  # no time_start/time_end in the model's response
    ))
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeImageResponse())

    soup = BeautifulSoup(_FLYER_HTML, "lxml")
    obs = SoWalCrawler()._parse_flyer_image(
        soup, "Crackings. - Grayton Beach",
        "https://sowal.com/event/live-music-crackings-541", "Live Music @ Crackings",
        fallback_time_start="6:00 pm", fallback_time_end="9:00 pm",
    )

    assert obs[0]["time_start"] == "6:00 pm"
    assert obs[0]["time_end"] == "9:00 pm"


def test_parse_flyer_image_never_overrides_a_time_start_vision_did_read(monkeypatch):
    # _VISION_JSON's time_start ("9:30 am") must win over the fallback. Its
    # time_end is irrelevant here: app.images.importer._normalise() always
    # sets time_end to None regardless of what the raw JSON says (the Vision
    # prompt never asks for one), so time_end always comes from the fallback
    # for flyer-sourced observations -- covered by the test above.
    _patch_vision(monkeypatch)
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeImageResponse())

    soup = BeautifulSoup(_FLYER_HTML, "lxml")
    obs = SoWalCrawler()._parse_flyer_image(
        soup, "Crackings. - Grayton Beach",
        "https://sowal.com/event/live-music-crackings-541", "Live Music @ Crackings",
        fallback_time_start="6:00 pm", fallback_time_end="9:00 pm",
    )

    assert obs[0]["time_start"] == "9:30 am"


def test_parse_flyer_image_empty_when_vision_finds_nothing(monkeypatch):
    _patch_vision(monkeypatch, vision_json="[]")
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeImageResponse())

    soup = BeautifulSoup(_FLYER_HTML, "lxml")
    obs = SoWalCrawler()._parse_flyer_image(
        soup, "Crackings", "https://sowal.com/event/x", "Live Music @ Crackings"
    )
    assert obs == []


# --- integration: a generic-title page with only a flyer image resolves ----

_CRACKINGS_PAGE_HTML = """
<html><body>
  <h1>Live Music @ Crackings</h1>
  <p>When: Wednesday, July 15, 2026</p>
  <p>Time: 9:30 am to 12:30 pm</p>
  <p>Where: Crackings. - Grayton Beach</p>
  <img class="image-_bohr-body-image-full-width margin-bottom-standard" src="/sites/default/files/2_116.jpg?itok=abc" />
</body></html>
"""


class _FakePageResponse:
    def __init__(self, text):
        self.text = text
        self.status_code = 200
        self.content = b""

    def raise_for_status(self):
        pass


def test_parse_event_observations_falls_back_to_flyer_when_title_is_generic(monkeypatch):
    _patch_vision(monkeypatch)

    import requests
    page_url = "https://sowal.com/event/live-music-crackings-541?date=2026-07-15"
    image_url = "https://sowal.com/sites/default/files/2_116.jpg?itok=abc"

    def fake_get(url, headers=None, timeout=None):
        if url == page_url:
            return _FakePageResponse(_CRACKINGS_PAGE_HTML)
        if url == image_url:
            return _FakeImageResponse()
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(requests, "get", fake_get)

    obs = SoWalCrawler().parse_event_observations(page_url)

    assert len(obs) == 1
    assert obs[0]["performer"] == "Laura Lane"
    assert obs[0]["performer_status"] == "named"
    assert obs[0]["venue"] == "Crackings. - Grayton Beach"
