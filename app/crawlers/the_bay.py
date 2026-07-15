"""
app/crawlers/the_bay.py
The Bay (Santa Rosa Beach) live music crawler.

The Bay's own website (baysouthwalton.com) embeds its "MARK YOUR CALENDARS!"
schedule via a third-party widget (eventcalendarapp.com, calendar id 10794)
that renders client-side -- no static HTML to scrape. The widget itself
calls a public, paginated JSON API that's the actual data source:
    https://api.eventcalendarapp.com/events?id=10794&widgetUuid=...
Calling it with no `page` param returns the page containing "today" (found
by watching the widget's own network requests), so fetch() just follows
`pages.nextPage` a bounded number of times rather than computing date/page
math itself. Each event's `timezoneStart`/`timezoneEnd` fields are already
local wall-clock ISO strings (confirmed against the site's own "4:00 PM -
8:00 PM CDT" display) -- no UTC conversion needed.

Two recurring event types observed on a live pull:
  - "Sunday Pickin' with {Name} & Friends" -- performer named right in the
    title, same "with X" pattern app.crawlers.sowal already handles.
  - "Wednesday Night Bonfire & Live Music" -- a generic title (no named act)
    whose *description* embeds a whole season's rotating lineup as loose,
    emoji-bulleted text ("2026 LINEUP: (note emoji) June 10 - Martin Lane
    (drum emoji) June 17 - Arrowgrass ..."), one paragraph covering many
    calendar occurrences rather than one entry per occurrence.
    _lineup_performer_for_date() splits that text on emoji boundaries
    (which reliably isolate each "Month Day - Name" entry) and looks up the
    specific occurrence's date; if the lineup text hasn't been updated to
    cover that date yet, the occurrence is skipped rather than guessing --
    same "never invent a performer" rule sowal.py and ajs_grayton.py follow.
"""
import logging
import re
from datetime import datetime

import requests

from app.crawlers.sowal import extract_performer_from_description, is_generic_title

logger = logging.getLogger(__name__)

EVENTS_API = "https://api.eventcalendarapp.com/events"
CALENDAR_ID = 10794
WIDGET_UUID = "24fdce06-bfc8-4f87-aab2-5801cc2adaa2"
VENUE = "The Bay"
MAX_PAGES = 8  # ~2 months out, matching this app's other crawlers' horizon

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137 Safari/537.36"
    )
}

# Splits lineup description text on emoji / pictographic runs (and the
# zero-width word joiner U+2060 seen between entries), which reliably
# isolates each "Month Day - Name" entry as its own chunk.
_EMOJI_SPLIT_RE = re.compile(r"[\U0001F300-\U0001FAFF☀-➿⁠]+")
_LINEUP_ENTRY_RE = re.compile(r"^([A-Za-z]+\s+\d{1,2})\s*-\s*(.+)$")
_LINEUP_YEAR_RE = re.compile(r"\b(20\d{2})\s*LINEUP\b", re.I)
_MONTH_DAY_FORMATS = ["%B %d %Y", "%b %d %Y"]
_LOCAL_DT_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):\d{2}$")


# ---------------------------------------------------------------------------
# Pure parsing helpers (unit-tested without network)
# ---------------------------------------------------------------------------

def _split_local_datetime(value: str | None) -> tuple[str | None, str | None]:
    """'2026-07-19T16:00:00' -> ('2026-07-19', '4:00 pm'). Already local, no tz math."""
    m = _LOCAL_DT_RE.match((value or "").strip())
    if not m:
        return None, None
    date, hour, minute = m.groups()
    h = int(hour) % 12 or 12
    ap = "am" if int(hour) < 12 else "pm"
    return date, f"{h}:{minute} {ap}"


def _strip_html(html: str | None) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    text = text.replace("&nbsp;", " ").replace("&rsquo;", "'").replace("&amp;", "&")
    return re.sub(r"\s+", " ", text).strip()


def _lineup_performer_for_date(description_html: str | None, date: str) -> str | None:
    """
    Look up a specific (month, day) inside a "20XX LINEUP: ... Month Day -
    Name ..." block embedded in a recurring event's description. Returns
    None if that date isn't (yet) listed -- never guesses.
    """
    text = _strip_html(description_html)
    if not text:
        return None
    year_match = _LINEUP_YEAR_RE.search(text)
    year = int(year_match.group(1)) if year_match else int(date[:4])

    for chunk in _EMOJI_SPLIT_RE.split(text):
        chunk = chunk.strip()
        m = _LINEUP_ENTRY_RE.match(chunk)
        if not m:
            continue
        when, name = m.group(1), m.group(2).strip()
        parsed = None
        for fmt in _MONTH_DAY_FORMATS:
            try:
                parsed = datetime.strptime(f"{when} {year}", fmt).date().isoformat()
                break
            except ValueError:
                continue
        if parsed == date and name:
            return name
    return None


def _extract_performer(summary: str | None, description_html: str | None, date: str) -> str | None:
    summary = (summary or "").strip()
    if not summary:
        return None
    name = extract_performer_from_description(summary)
    if name:
        return name
    if is_generic_title(summary):
        return _lineup_performer_for_date(description_html, date)
    return summary


def parse_events(payload: dict) -> list[dict]:
    """Parse one page of the events API response into raw event dicts."""
    events: list[dict] = []
    for ev in payload.get("events", []):
        date, time_start = _split_local_datetime(ev.get("timezoneStart"))
        _end_date, time_end = _split_local_datetime(ev.get("timezoneEnd"))
        if not date:
            continue

        performer = _extract_performer(ev.get("summary"), ev.get("longDescription"), date)
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
            "url":        ev.get("url"),
            "source":     "the_bay",
            "observation_type": "website",
        })
    return events


class TheBayCrawler:
    name = "the_bay"

    def fetch(self) -> list[dict]:
        events: list[dict] = []
        url = f"{EVENTS_API}?id={CALENDAR_ID}&widgetUuid={WIDGET_UUID}"

        for _ in range(MAX_PAGES):
            if not url:
                break
            try:
                response = requests.get(url, headers=HEADERS, timeout=20)
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:
                logger.warning("[TheBayCrawler] %s", exc)
                break
            events.extend(parse_events(payload))
            url = (payload.get("pages") or {}).get("nextPage")

        logger.info("[TheBayCrawler] Parsed %d events", len(events))
        return events
