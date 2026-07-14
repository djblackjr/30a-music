"""
app/crawlers/shunk_gulley.py
Shunk Gulley live music crawler.

Shunk Gulley's own website (shunkgulley.com/LIVE-MUSIC-ON-30A) doesn't host
its schedule as page content -- it embeds a Tockify calendar widget
(data-tockify-calendar="shunk") that renders client-side, so there's no
static HTML to scrape. Tockify publishes that same calendar as a public
iCalendar feed, which is the widget's actual data source and far more
stable to parse than the rendered widget would be:

    https://tockify.com/api/feeds/ics/shunk

Every event on this feed is at Shunk Gulley (a single-venue feed, no
LOCATION field) with one named performer per SUMMARY -- confirmed against a
live pull, no generic "Live Music" placeholders -- so observations pass
straight through to normalize_events with no performer_status classification
(see the classifiable/passthrough split in app/monitor.py).

DTSTART/DTEND are UTC; converted to the feed's declared X-WR-TIMEZONE
(America/Chicago) via zoneinfo, which also gets DST right across the season.
"""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

ICS_URL = "https://tockify.com/api/feeds/ics/shunk"
VENUE = "Shunk Gulley"
_UTC = ZoneInfo("UTC")
_LOCAL_TZ = ZoneInfo("America/Chicago")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137 Safari/537.36"
    )
}


# ---------------------------------------------------------------------------
# Pure parsing helpers (unit-tested without network)
# ---------------------------------------------------------------------------

def _unfold(text: str) -> list[str]:
    """RFC 5545 line unfolding: a line starting with a space/tab continues the previous line."""
    lines: list[str] = []
    for raw in text.splitlines():
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _unescape(value: str) -> str:
    return (
        value.replace("\\N", "\n").replace("\\n", "\n")
        .replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")
    )


def _parse_utc(value: str) -> datetime | None:
    """'20260613T220000Z' -> aware UTC datetime, or None if unparseable/not UTC."""
    if not value.endswith("Z"):
        return None
    try:
        return datetime.strptime(value.strip(), "%Y%m%dT%H%M%SZ").replace(tzinfo=_UTC)
    except ValueError:
        return None


def _fmt_time(dt: datetime) -> str:
    """12-hour clock, no leading zero, lowercase am/pm -- e.g. '5:00 pm'."""
    return dt.strftime("%I:%M %p").lstrip("0").lower()


def parse_ics(text: str) -> list[dict]:
    """Parse a Tockify ICS feed into raw event dicts, one per VEVENT."""
    events: list[dict] = []
    current: dict[str, str] | None = None

    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            current = {}
            continue
        if line == "END:VEVENT":
            if current is not None:
                ev = _assemble(current)
                if ev:
                    events.append(ev)
            current = None
            continue
        if current is None or ":" not in line:
            continue
        key, _, value = line.partition(":")
        current[key.split(";")[0]] = value  # drop parameters, e.g. DTSTART;TZID=...

    return events


def _assemble(fields: dict[str, str]) -> dict | None:
    performer = _unescape(fields.get("SUMMARY", "")).strip()
    start = _parse_utc(fields.get("DTSTART", ""))
    if not performer or not start:
        return None

    start_local = start.astimezone(_LOCAL_TZ)
    end = _parse_utc(fields.get("DTEND", ""))
    end_local = end.astimezone(_LOCAL_TZ) if end else None

    return {
        "name":       f"{performer} at {VENUE}",
        "performer":  performer,
        "venue":      VENUE,
        "date":       start_local.date().isoformat(),
        "time_start": _fmt_time(start_local),
        "time_end":   _fmt_time(end_local) if end_local else None,
        "stage":      None,
        "url":        fields.get("URL") or None,
        "source":     "shunk_gulley",
        "observation_type": "website",
    }


# ---------------------------------------------------------------------------
# Crawler
# ---------------------------------------------------------------------------

class ShunkGulleyCrawler:
    name = "shunk_gulley"

    def fetch(self) -> list[dict]:
        try:
            response = requests.get(ICS_URL, headers=HEADERS, timeout=20)
            response.raise_for_status()
        except Exception as exc:
            logger.warning("[ShunkGulleyCrawler] %s", exc)
            return []

        events = parse_ics(response.text)
        logger.info("[ShunkGulleyCrawler] Parsed %d events from Tockify feed", len(events))
        return events
