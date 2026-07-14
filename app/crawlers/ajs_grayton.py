"""
app/crawlers/ajs_grayton.py
AJ's Grayton Beach live music crawler.

AJ's events page (ajsgrayton.com/.../events-and-music) is server-rendered
HTML from a "Spot" events widget (static.spotapps.co) -- no client-side
rendering to work around, unlike Shunk Gulley's Tockify widget. Each event
is a <section id="{event_id}"> containing an "add to calendar" block with
machine-readable fields:
    <var class="atc_date_start">2026-07-19 17:00:00</var>
    <var class="atc_date_end">2026-07-19 20:00:00</var>
    <var class="atc_timezone">America/Chicago</var>
    <var class="atc_title">...</var>
    <var class="atc_location">AJ's Grayton Beach</var>
That date/time is already a naive local wall-clock time in the venue's own
timezone (confirmed always America/Chicago on a live pull) -- no UTC
conversion needed, unlike the Shunk Gulley/Tockify feed.

The widget lists ALL of AJ's events, not just music (a live pull turned up
a golf tournament, a happy-hour promo, and a meetup alongside the live
music), and titles don't follow one convention:
    "Live Music with Kevin Carson at AJ's Grayton Beach"  -> Kevin Carson
    "Live Music: Dion Jones & The Neon Tears"              -> Dion Jones & The Neon Tears
    "Jim Couch - Thursday Nights"                          -> Jim Couch
    "3HG"                                                  -> 3HG (bare act name)
    "Karaoke"                                              -> skipped (category, not an act)
    "Teachers Back-to-School Happy Hour at AJ's"           -> skipped (not music)

_extract_performer() below reuses app.crawlers.sowal's tested "with X" /
"featuring X" extractor and generic/category detectors rather than
duplicating their name-cleaning regex -- including for the bare-title case
("3HG"), which is routed through the same extractor via a synthetic "with "
prefix so a short/non-name-shaped title (starting with a digit, say) still
falls back to using the raw title rather than silently producing nothing.
"""
import logging
import re

import requests
from bs4 import BeautifulSoup

from app.crawlers.sowal import detect_category, extract_performer_from_description, is_generic_title

logger = logging.getLogger(__name__)

EVENTS_URL = "https://ajsgrayton.com/santa-rosa-beach-grayton-beach-aj-s-grayton-beach-events-and-music"
VENUE = "AJ's Grayton Beach"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137 Safari/537.36"
    )
}

# Non-music promo events seen mixed into the same widget as the live music
# (golf tournament, happy hour, meetup) -- not exhaustive, just what's been
# observed; same "curated, narrow list" approach as sowal.py's _GENERIC_WORDS.
_NON_MUSIC_RE = re.compile(
    r"\b(happy hour|golf tournament|meet ?up|fundraiser|wine tasting|beer tasting)\b",
    re.I,
)

_DATETIME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2}):(\d{2})$")


# ---------------------------------------------------------------------------
# Pure parsing helpers (unit-tested without network)
# ---------------------------------------------------------------------------

def _split_datetime(value: str) -> tuple[str | None, str | None]:
    """'2026-07-19 17:00:00' -> ('2026-07-19', '5:00 pm'). Naive local time, no tz math."""
    m = _DATETIME_RE.match((value or "").strip())
    if not m:
        return None, None
    year, month, day, hour, minute, _sec = m.groups()
    h = int(hour) % 12 or 12
    ap = "am" if int(hour) < 12 else "pm"
    return f"{year}-{month}-{day}", f"{h}:{minute} {ap}"


def _extract_performer(title: str | None) -> str | None:
    title = (title or "").strip()
    if not title or detect_category(title) or _NON_MUSIC_RE.search(title):
        return None

    # "... with {Name} ..." / "... featuring {Name} ..." cue in the title
    name = extract_performer_from_description(title)
    if name:
        return name

    # "{Category}: {Name}" e.g. "Live Music: Dion Jones & The Neon Tears" --
    # the colon already isolates the act; used as-is rather than re-run
    # through the with-pattern extractor, whose name capture is capped at a
    # handful of tokens and would truncate a longer band name.
    if ":" in title:
        _prefix, _, tail = title.partition(":")
        tail = tail.strip()
        if tail and not is_generic_title(tail) and not detect_category(tail):
            return tail

    # No cue at all -- the whole title may just BE the act's name (e.g.
    # "3HG"). Route it through the same with-pattern extractor via a
    # synthetic prefix so a name the extractor's regex can't clean (e.g.
    # one starting with a digit) still falls back to the raw title instead
    # of silently producing nothing.
    if is_generic_title(title):
        return None
    return extract_performer_from_description(f"with {title}") or title


def parse_events(html: str) -> list[dict]:
    """Parse the AJ's events page into raw event dicts, one per <section>."""
    soup = BeautifulSoup(html, "lxml")
    events: list[dict] = []

    for section in soup.find_all("section", id=True):
        atc = section.find("var", class_="atc_event")
        if not atc:
            continue

        def _var(cls: str) -> str:
            el = atc.find("var", class_=cls)
            return el.get_text(strip=True) if el else ""

        title = _var("atc_title")
        date, time_start = _split_datetime(_var("atc_date_start"))
        _end_date, time_end = _split_datetime(_var("atc_date_end"))
        if not date:
            continue

        performer = _extract_performer(title)
        if not performer:
            continue

        events.append({
            "name":       f"{performer} at {VENUE}",
            "performer":  performer,
            "venue":      VENUE,
            "date":       date,
            "time_start": time_start,
            "time_end":   time_end,
            "stage":      None,
            "url":        f"{EVENTS_URL}#{section['id']}",
            "source":     "ajs_grayton",
            "observation_type": "website",
        })

    return events


# ---------------------------------------------------------------------------
# Crawler
# ---------------------------------------------------------------------------

class AJsGraytonCrawler:
    name = "ajs_grayton"

    def fetch(self) -> list[dict]:
        try:
            response = requests.get(EVENTS_URL, headers=HEADERS, timeout=20)
            response.raise_for_status()
        except Exception as exc:
            logger.warning("[AJsGraytonCrawler] %s", exc)
            return []

        events = parse_events(response.text)
        logger.info("[AJsGraytonCrawler] Parsed %d events", len(events))
        return events
