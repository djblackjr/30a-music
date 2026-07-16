"""
app/crawlers/sowal.py
SoWal events crawler.

Crawls https://sowal.com/events and extracts one OBSERVATION per calendar row
(performer, venue, ISO date, start/end time) plus provenance/evidence about how
each was read.

The listing page groups events into one `<table class="views-table">` per
date, each row holding a time (range or single) and a title/link — every date
and time for every event instance is already present in this ONE response, so
`fetch()` parses it directly instead of following each event link.

Only titles that can't be resolved from the title alone (an unresolved/category
performer, or a title with no ' @ Venue') fall back to fetching a page — one
representative URL per distinct title (not per date instance), since recurring
rows share the same title/venue/description:
    <h1>Duncan Crittenden @ Local Catch Bar & Grill</h1>
    When:  Saturday, July 11, 2026
    Time:  5:00 pm  to  8:00 pm
    Where: Local Catch Bar & Grill

Some pages are NOT a single named act:
  - a GENERIC live-music title with no act    ("Live Music", "Brunch & Live Music")
  - a CATEGORY event                          ("DJ Night", "Karaoke", "Open Mic")
  - a LINEUP / series listing many performers on many dates

Each emitted observation carries an explicit classification so downstream code
can keep named artist events separate from unresolved and category events
(see partition_observations); the crawler never invents a fake performer such as
"Live Music" to keep a record.

Public contract (preserved):
    parse_event(url) -> dict | None      # first observation, or None
New:
    parse_event_observations(url) -> list[dict]   # 0..N observations per page

Duck-typed to the crawler protocol (has `name` and `fetch()`); registered in
app/crawlers/registry.py. Named observations flow through app/normalize before
save; unresolved/category observations are partitioned out first.
"""
import logging
import os
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from app.crawlers.policy import CrawlPolicy

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure parsing helpers (unit-tested without network)
# ---------------------------------------------------------------------------

# Date formats seen on SoWal event pages, most specific first.
_WHEN_FORMATS = ["%A, %B %d, %Y", "%B %d, %Y", "%A %B %d, %Y", "%m/%d/%Y", "%Y-%m-%d"]

_TIME_RANGE_RE  = re.compile(r"(\d{1,2}:\d{2}\s*[ap]m)\s*to\s*(\d{1,2}:\d{2}\s*[ap]m)", re.I)
_TIME_LABEL_RE  = re.compile(r"Time:\s*(.+)")
_BARE_TIME_RE   = re.compile(r"(\d{1,2}:\d{2}\s*[ap]m)", re.I)


def split_title(title: str | None) -> tuple[str, str | None]:
    """'Performer @ Venue' -> ('Performer', 'Venue'); no ' @ ' -> (title, None)."""
    if not title:
        return "", None
    if " @ " in title:
        performer, venue = title.split(" @ ", 1)
        return performer.strip(), venue.strip() or None
    return title.strip(), None


def parse_when(raw: str | None) -> str | None:
    """Parse a SoWal 'When:' string into an ISO date, or None if unparseable."""
    if not raw:
        return None
    s = raw.strip()
    for fmt in _WHEN_FORMATS:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_time(text: str) -> tuple[str | None, str | None]:
    """Return (start, end). Prefers an explicit 'X to Y' range, else a single time."""
    m = _TIME_RANGE_RE.search(text)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    m = _TIME_LABEL_RE.search(text)
    if m:
        return m.group(1).split("\n")[0].strip(), None
    return None, None


# ---------------------------------------------------------------------------
# Classification helpers (pure; unit-tested without network)
# ---------------------------------------------------------------------------

# Words that, standing alone, denote a live-music event with no named act.
_GENERIC_WORDS: set[str] = {
    "live", "music", "band", "entertainment", "concert", "series",
    "brunch", "bonfire", "dinner", "sunset", "sunrise", "patio", "deck",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "summer", "spring", "fall", "autumn", "winter",
    "weekly", "nightly", "daily", "night", "weekend", "afternoon", "evening",
    "lineup", "schedule", "tba", "tbd",
}
# Connectors that don't disqualify a title from being generic.
_TITLE_FILLER: set[str] = {"&", "and", "the", "a", "an", "with", "on", "at", "of", "-", "–", "·", "/"}

# Category events are NOT unresolved artists — they are their own event kind.
_CATEGORY_PATTERNS: list[tuple[str, "re.Pattern"]] = [
    ("dj",       re.compile(r"\bdj\b", re.I)),
    ("karaoke",  re.compile(r"\bkaraoke\b", re.I)),
    ("open_mic", re.compile(r"\bopen[\s-]?mic\b", re.I)),
    ("trivia",   re.compile(r"\btrivia\b", re.I)),
]

# SoWal aggregates every Walton County community-calendar listing, not just
# live music (farmers markets, state-park tours, car shows, ...) -- none of
# these have a "named act", but they aren't generic-live-music titles either
# (they contain real proper nouns), so without this check they fall through
# to "whole title is the performer" and show up as if e.g. "DeFuniak Springs
# Farmers Market" were a band. Narrow, curated list in the same spirit as
# _CATEGORY_PATTERNS above -- not exhaustive, just what's been observed. Each
# addition here was verified against its actual SoWal description first (see
# the "Moon Crush" / "Panama City Songwriters Festival" tests below for the
# opposite case: real multi-artist music festivals with no single named act,
# which must NOT be caught by these patterns).
_NON_MUSIC_PATTERNS: list[tuple[str, "re.Pattern"]] = [
    ("farmers_market", re.compile(r"farmers?\s*market", re.I)),
    ("guided_tour",    re.compile(r"guided\s+\w*\s*(tour|hike|walk)|ranger[- ]?guided|nature\s+hike|history\s+tour", re.I)),
    ("car_show",       re.compile(r"\bcar\s+show\b|\bcars\s+of\s+30a\b", re.I)),
    # "day run" catches holiday/charity races named e.g. "Thanksgiving Day Run",
    # "Turkey Day Run", "Memorial Day Run" -- a common naming convention this
    # crawler was missing (confirmed via a real "30A Thanksgiving Day Run"
    # listing that slipped through as a blank-venue event on the dashboard).
    ("sporting_event", re.compile(r"\bironman\b|\btriathlon\b|\bmarathon\b|\b\d+k\s+run\b|\bfun run\b|\bday\s+run\b", re.I)),
    # Widened to also match "Airshow" as one word (was "air show" only,
    # missing "Pensacola Beach Airshow: US Navy Blue Angels").
    ("air_show",       re.compile(r"\bair\s*show\b", re.I)),
    # Widened to also catch "Wine & Food Festival" / "Wine and Food Festival"
    # variants (was "wine festival" only, missing "Harvest Wine & Food
    # Festival: ..." listings).
    ("wine_festival",  re.compile(r"\bwine\s*(?:&|and)?\s*food\s+festival\b|\bwine\s+festival\b", re.I)),
    ("film_festival",  re.compile(r"\bmountainfilm\b|\bfilm festival\b", re.I)),
    ("eggfest",        re.compile(r"\beggs on the beach\b|\beggfest\b", re.I)),
    ("county_fair",    re.compile(r"\bcounty\s+fair\b", re.I)),
    ("wine_tasting",   re.compile(r"\buncorked\b", re.I)),
    ("award_ceremony", re.compile(r"\bseaside prize\b", re.I)),
]

# Strong, unambiguous performer indicators in free text.
_DESC_STRONG_RE = re.compile(
    r"\b(?:featuring|feat\.|ft\.|presents|performance by|music by)\s+(.+)", re.I
)
# 'with' is accepted only when what follows clearly reads as a credited act.
_DESC_WITH_RE = re.compile(r"\bwith\s+(.+)", re.I)

# A performer name: capitalised tokens, optionally joined by &/and/the/of.
_NAME_RE = re.compile(r"^([A-Z][\w.'’-]*(?:\s+(?:&|and|the|of|[A-Z][\w.'’-]*)){0,4})")

# Single lower-cased words that mean the phrase is prose, not a performer.
_NAME_STOPWORDS: set[str] = {
    "friends", "family", "dinner", "lunch", "brunch", "food", "drinks",
    "cocktails", "everyone", "guests", "us", "you", "your",
}


def _title_tokens(value: str | None) -> list[str]:
    return [t for t in re.split(r"[^\w&]+", (value or "").lower()) if t]


def is_generic_title(title_part: str | None) -> bool:
    """True when the title denotes live music with no named act (narrow)."""
    tokens = _title_tokens(title_part)
    if not tokens:
        return False
    return all(t in _GENERIC_WORDS or t in _TITLE_FILLER for t in tokens)


def detect_category(title_part: str | None) -> str | None:
    """Return dj/karaoke/open_mic/trivia if the title is such an event, else None."""
    s = title_part or ""
    for category, rx in _CATEGORY_PATTERNS:
        if rx.search(s):
            return category
    return None


def detect_non_music(title_part: str | None) -> str | None:
    """Return a non-music category (farmers_market/guided_tour/car_show) or None."""
    s = title_part or ""
    for category, rx in _NON_MUSIC_PATTERNS:
        if rx.search(s):
            return category
    return None


def _clean_name(candidate: str | None) -> str | None:
    """Trim a captured phrase to a plausible performer name, or return None."""
    if not candidate:
        return None
    candidate = re.split(r"[,.;:()\n]", candidate)[0].strip()
    m = _NAME_RE.match(candidate)
    if not m:
        return None
    name = re.sub(r"\s+(?:&|and|the|of)$", "", m.group(1).strip()).strip()
    if not name:
        return None
    # A generic phrase ("Live Music", "Live Band") is not a performer.
    if is_generic_title(name):
        return None
    if name.lower() in _NAME_STOPWORDS:
        return None
    return name


def extract_performer_from_description(text: str | None) -> str | None:
    """
    Conservatively recover a named performer from free text.

    Strong indicators (featuring / feat. / ft. / presents / performance by /
    music by) are always considered. 'with' is honoured only when the following
    phrase clearly reads as a credited act (a Title-Case name), so ordinary
    prose like "brunch with friends" or "music with dinner" yields nothing.
    """
    if not text:
        return None
    m = _DESC_STRONG_RE.search(text)
    if m:
        name = _clean_name(m.group(1))
        if name:
            return name
    m = _DESC_WITH_RE.search(text)
    if m:
        name = _clean_name(m.group(1))
        if name:
            return name
    return None


def classify_performer(title: str | None, description: str = "") -> dict:
    """
    Classify a page's performer from its title, falling back to the description.

    Returns keys: performer, performer_status, resolved, event_category,
    extraction_method. A category event only becomes 'named' when a performer is
    explicitly present; a generic title only becomes 'named' when one is
    recovered from the description. Otherwise no fake performer is invented.

    A detected non-music event (farmers market, guided tour, car show, ...)
    is excluded outright -- unlike DJ/karaoke, its description was never
    going to name a musical act, so there's no description fallback here.
    """
    perf_part, _venue = split_title(title)

    non_music = detect_non_music(perf_part)
    if non_music:
        return {"performer": None, "performer_status": "category", "resolved": False,
                "event_category": non_music, "extraction_method": "non_music"}

    category = detect_category(perf_part)
    if category:
        name = extract_performer_from_description(description)
        if name:
            return {"performer": name, "performer_status": "named", "resolved": True,
                    "event_category": category, "extraction_method": "description"}
        return {"performer": None, "performer_status": "category", "resolved": False,
                "event_category": category, "extraction_method": "category"}

    if is_generic_title(perf_part):
        name = extract_performer_from_description(description)
        if name:
            return {"performer": name, "performer_status": "named", "resolved": True,
                    "event_category": "live_music", "extraction_method": "description"}
        return {"performer": None, "performer_status": "unresolved", "resolved": False,
                "event_category": "live_music", "extraction_method": "unresolved"}

    return {"performer": perf_part, "performer_status": "named", "resolved": True,
            "event_category": "live_music", "extraction_method": "title"}


# ---------------------------------------------------------------------------
# Prose lineup handling
#
# A recurring-series title with no ' @ Venue' split (e.g. "Baytowne Wednesday
# Night Concert Series", "30Avenue Summer Concert Series", "Harbor Nights at
# HarborWalk") describes a PROGRAM, not a single act -- but is_generic_title
# can't tell, because the venue's own proper noun (Baytowne, 30Avenue,
# HarborWalk) breaks the "every token is a generic word" check, so
# classify_performer falls through to its "whole title is the performer"
# catch-all and invents a fake performer out of the series name.
#
# Two independent signals recover the real per-date answer:
#   - the description often embeds the actual lineup as inline prose, e.g.
#     "July 15th: The Aces Band" -- parse_prose_lineup() + resolve_performer()
#     below match that against the target date.
#   - failing that, a page that explicitly says "see the full lineup below"
#     is telling us the real answer is off-page (usually a flyer image) --
#     _points_to_external_lineup() flags this so callers can downgrade a
#     title-guessed "named" result to unresolved rather than trust it.
# ---------------------------------------------------------------------------

_MONTH_NAMES = (
    r"January|February|March|April|May|June|July|"
    r"August|September|October|November|December"
)
_PROSE_LINEUP_ENTRY_RE = re.compile(
    rf"\b({_MONTH_NAMES})\s+(\d{{1,2}})(?:st|nd|rd|th)?\s*:\s*"
    rf"(.+?)(?=\b(?:{_MONTH_NAMES})\s+\d{{1,2}}(?:st|nd|rd|th)?\s*:|$)",
    re.I,
)
_LINEUP_POINTER_RE = re.compile(
    r"\b(?:see|check out)\s+(?:the\s+)?(?:full\s+|music\s+|complete\s+)?line[\s-]?up\b", re.I
)


def parse_prose_lineup(description: str | None, year: int | None) -> dict[str, str]:
    """
    Parse an inline 'Month Day[st/nd/rd/th]: Name' lineup embedded directly in
    a description's prose (as opposed to a table), e.g. "July 15th: The Aces
    Band". Returns {iso_date: name_text}. Best-effort: an entry whose own
    name text happens to contain something resembling the next-entry boundary
    (rare, seen once as "...Gage Cowart THURSDAY, July 2, Western Green: Will
    Thompson Band...") may absorb extra text, but that only corrupts entries
    this function isn't asked to match against.
    """
    if not description or year is None:
        return {}
    entries: dict[str, str] = {}
    for m in _PROSE_LINEUP_ENTRY_RE.finditer(description):
        month, day, name = m.group(1), m.group(2), m.group(3).strip(" -—")
        if not name:
            continue
        try:
            date_val = datetime.strptime(f"{month} {day} {year}", "%B %d %Y").date().isoformat()
        except ValueError:
            continue
        entries[date_val] = name
    return entries


def _points_to_external_lineup(description: str | None) -> bool:
    """True when the page explicitly defers to a lineup we haven't found in text."""
    return bool(_LINEUP_POINTER_RE.search(description or ""))


def _classify_prose_lineup_entry(name: str) -> dict:
    """Classify one 'Month Day: <name>' entry the same way classify_performer would."""
    category = detect_category(name)
    if category:
        performer = extract_performer_from_description(name)
        if performer:
            return {"performer": performer, "performer_status": "named", "resolved": True,
                    "event_category": category, "extraction_method": "prose_lineup"}
        return {"performer": None, "performer_status": "category", "resolved": False,
                "event_category": category, "extraction_method": "prose_lineup"}

    if is_generic_title(name) or name.strip().upper() in ("TBA", "TBD"):
        return {"performer": None, "performer_status": "unresolved", "resolved": False,
                "event_category": "live_music", "extraction_method": "prose_lineup"}

    return {"performer": name, "performer_status": "named", "resolved": True,
            "event_category": "live_music", "extraction_method": "prose_lineup"}


def resolve_performer(
    title: str | None, description: str, target_date: str | None, page_year: int | None = None
) -> dict:
    """
    classify_performer(), refined with the two prose-lineup signals above.

    An exact per-date match from an inline prose lineup always wins (it's
    more specific than any title-based guess). Otherwise, a title that was
    only resolved via the "whole title is the performer" catch-all is
    downgraded to unresolved when the page explicitly points to a lineup we
    couldn't find in text -- callers can then fall back to e.g. flyer-image
    extraction instead of saving the series name as a fake performer.
    """
    c = classify_performer(title, description)

    if target_date:
        name = parse_prose_lineup(description, page_year).get(target_date)
        if name:
            return _classify_prose_lineup_entry(name)

    if c["extraction_method"] == "title" and _points_to_external_lineup(description):
        return {"performer": None, "performer_status": "unresolved", "resolved": False,
                "event_category": "live_music", "extraction_method": "unresolved"}

    return c


def _cell_time(cell: str) -> tuple[str | None, str | None]:
    """Time from a lineup cell: an 'X to Y' range, else a single bare time."""
    m = _TIME_RANGE_RE.search(cell)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    m = _BARE_TIME_RE.search(cell)
    if m:
        return m.group(1).strip(), None
    return None, None


def parse_lineup_date(cell: str, page_year: int | None = None) -> str | None:
    """
    Parse a lineup-row date. Falls back to the PAGE's year only when the cell has
    no explicit year of its own; returns None if no confident date can be formed
    (never guesses a year from anything but the page context).
    """
    d = parse_when(cell)
    if d:
        return d
    if page_year and not re.search(r"\b\d{4}\b", cell):
        d = parse_when(f"{cell.strip()}, {page_year}")
        if d:
            return d
    return None


def partition_observations(observations: list[dict]) -> dict:
    """
    Split raw observations into three collections so that only real named
    artists are sent through named-event normalisation:
      - named:      resolved artist observations (-> normalize_events)
      - unresolved: generic live-music with no recoverable act
      - category:   DJ / karaoke / open-mic / trivia events
    """
    named, unresolved, category = [], [], []
    for o in observations:
        status = o.get("performer_status")
        if status == "named" and o.get("resolved"):
            named.append(o)
        elif status == "category":
            category.append(o)
        else:
            unresolved.append(o)
    return {"named": named, "unresolved": unresolved, "category": category}


# ---------------------------------------------------------------------------
# Crawler
# ---------------------------------------------------------------------------

class SoWalCrawler:
    name = "sowal"

    START_URL = "https://sowal.com/events"

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137 Safari/537.36"
        )
    }

    def __init__(self, policy: CrawlPolicy | None = None):
        # Strategy is injected; default is exhaustive and polite (see CrawlPolicy).
        self.policy = policy or CrawlPolicy()

    def fetch(self) -> list[dict]:
        logger.info("[SoWalCrawler] Crawling %s", self.START_URL)

        try:
            response = requests.get(self.START_URL, headers=self.HEADERS, timeout=20)
            response.raise_for_status()
        except Exception as exc:
            logger.warning("[SoWalCrawler] %s", exc)
            return []

        soup = BeautifulSoup(response.text, "lxml")

        # The listing page's date-grouped calendar tables already carry every
        # date/time/title for every event instance in this one response — no
        # need to follow each event link just to learn date/time.
        rows = self._parse_calendar_rows(soup)
        logger.info("[SoWalCrawler] Parsed %d calendar rows", len(rows))

        classified = []
        for row in rows:
            title = row["title"]
            c = classify_performer(title, "")
            _perf, venue = split_title(title)
            classified.append({**row, **c, "venue": venue})

        # Only titles that couldn't be resolved from the title alone (an
        # unresolved/category performer, or a missing venue) need a page
        # fetch — one representative URL per distinct title, not per date
        # instance, since recurring rows share the same title/venue/description.
        candidates: dict[str, str] = {}
        for row in classified:
            needs_enrichment = row["performer_status"] in ("unresolved", "category") or row["venue"] is None
            if needs_enrichment and row["title"] not in candidates:
                candidates[row["title"]] = row["url"]

        candidate_items = self.policy.limit(list(candidates.items()))
        logger.info(
            "[SoWalCrawler] %d distinct titles need enrichment; fetching %d per policy",
            len(candidates), len(candidate_items),
        )

        enrichment: dict[str, dict] = {}
        for i, (title, url) in enumerate(candidate_items):
            if i and self.policy.request_delay:
                time.sleep(self.policy.request_delay)
            result = self._fetch_enrichment(url)
            if result:
                enrichment[title] = result

        events: list[dict] = []
        for row in classified:
            title = row["title"]
            performer, performer_status, resolved = row["performer"], row["performer_status"], row["resolved"]
            event_category, extraction_method = row["event_category"], row["extraction_method"]
            venue = row["venue"]
            description = ""

            enriched = enrichment.get(title)
            if enriched:
                # A "named" result reached via the title-only catch-all with no
                # '@ Venue' split is a guess, not a resolved artist (e.g.
                # "Baytowne Wednesday Night Concert Series") -- worth
                # re-checking against the now-fetched description too.
                title_guess_unverified = extraction_method == "title" and venue is None
                if venue is None and enriched["venue"]:
                    venue = enriched["venue"]
                if performer_status in ("unresolved", "category") or title_guess_unverified:
                    page_year = int(row["date"][:4]) if row["date"] else None
                    c = resolve_performer(title, enriched["description"], row["date"], page_year)
                    performer, performer_status = c["performer"], c["performer_status"]
                    resolved, event_category, extraction_method = (
                        c["resolved"], c["event_category"], c["extraction_method"],
                    )
                description = enriched["description"]

            events.append(self._assemble(
                performer=performer, venue=venue, date=row["date"],
                time_start=row["time_start"], time_end=row["time_end"], url=row["url"],
                title_raw=title, description_raw=description,
                extraction_method=extraction_method, performer_status=performer_status,
                resolved=resolved, event_category=event_category,
            ))

        logger.info("[SoWalCrawler] Parsed %d observations", len(events))
        return events

    # -- calendar-table parsing ---------------------------------------------

    @staticmethod
    def _absolutize(href: str) -> str:
        return "https://sowal.com" + href if href.startswith("/") else href

    def _parse_calendar_rows(self, soup) -> list[dict]:
        """
        Parse the listing's date-grouped calendar tables into raw rows.

        Each date has its own `<table class="views-table">` with a caption
        holding the date and one `<tr>` per event instance: a time cell
        (range or single time) and a title cell with the event's link.
        """
        rows: list[dict] = []
        for table in soup.find_all("table", class_="views-table"):
            caption = table.find("caption")
            if not caption:
                continue
            date_val = parse_when(caption.get_text(strip=True))
            if not date_val:
                continue
            for tr in table.find_all("tr"):
                tds = tr.find_all("td")
                if len(tds) < 2:
                    continue
                a = tds[1].find("a", href=True)
                if not a:
                    continue
                time_start, time_end = _cell_time(tds[0].get_text(" ", strip=True))
                rows.append({
                    "date": date_val,
                    "time_start": time_start,
                    "time_end": time_end,
                    "title": a.get_text(strip=True),
                    "url": self._absolutize(a["href"]),
                })
        return rows

    def _parse_more_events_widget(
        self, soup, own_date: str | None, default_venue: str | None
    ) -> list[dict]:
        """
        Event pages often carry a "More Events at <venue/series>" widget
        below the main content (Drupal view 'date_calendar_lists'): one
        `<table class="views-table">` per date, in the exact same
        caption-plus-rows shape as the main listing's calendar tables, each
        row linking to its own distinct event node for that date. Reuses
        _parse_calendar_rows, which already knows this shape.

        This widget was initially mistaken for junk (see
        _in_calendar_widget) because its per-table <caption> dates went
        unread; on a page with several distinct show titles at one venue
        (e.g. Old Florida Fish House: "Dueling Pianos", "Jake & Aimee"),
        it's real, useful, otherwise-unreachable data.

        Classifies each row from its title alone, with no further page
        fetch (kept cheap): a genuine "Act @ Venue" title resolves
        normally, but a repeated series title with no ' @ Venue' split
        (e.g. "30Avenue Summer Concert Series" appearing on every row) hits
        the same fake-performer pattern resolve_performer() guards against
        on the page's own title -- with no per-row description available to
        recover a real name from, that row is skipped rather than saving a
        fabricated performer.
        """
        obs = []
        for row in self._parse_calendar_rows(soup):
            if row["date"] == own_date:
                continue  # the page's own instance, already covered elsewhere

            title = row["title"]
            _perf, split_venue = split_title(title)
            c = classify_performer(title, "")
            if c["extraction_method"] == "title" and split_venue is None:
                continue

            obs.append(self._assemble(
                performer=c["performer"], venue=split_venue or default_venue, date=row["date"],
                time_start=row["time_start"], time_end=row["time_end"], url=row["url"],
                title_raw=title, description_raw="",
                extraction_method=c["extraction_method"], performer_status=c["performer_status"],
                resolved=c["resolved"], event_category=c["event_category"],
            ))
        return obs

    def _fetch_enrichment(self, url: str) -> dict | None:
        """Fetch one representative event page for a title: its venue + description."""
        try:
            response = requests.get(url, headers=self.HEADERS, timeout=20)
            response.raise_for_status()
        except Exception:
            return None

        soup = BeautifulSoup(response.text, "lxml")
        text = soup.get_text("\n", strip=True)
        h1 = soup.find("h1")
        title = h1.get_text(strip=True) if h1 else ""
        venue, description = self._extract_venue_and_description(soup, text, title)
        return {"venue": venue, "description": description}

    # -- observation extraction --------------------------------------------

    def parse_event(self, url: str) -> dict | None:
        """
        Backward-compatible single-observation view: the first observation on the
        page, or None. New code should prefer parse_event_observations().
        """
        obs = self.parse_event_observations(url)
        return obs[0] if obs else None

    def parse_event_observations(self, url: str) -> list[dict]:
        """Return every observation on an event page (0..N)."""
        try:
            response = requests.get(url, headers=self.HEADERS, timeout=20)
            response.raise_for_status()
        except Exception:
            return []

        soup = BeautifulSoup(response.text, "lxml")
        text = soup.get_text("\n", strip=True)

        h1 = soup.find("h1")
        title = h1.get_text(strip=True) if h1 else ""

        venue, description = self._extract_venue_and_description(soup, text, title)

        # Page-level 'When:' date — the year context for lineup rows, and the
        # date for a single-observation page.
        when = None
        m = re.search(r"When:\s*(.+)", text)
        if m:
            when = m.group(1).split("\n")[0].strip()
        page_date = parse_when(when)
        page_year = int(page_date[:4]) if page_date else None

        # "More Events at X": a separate set of dates this page links forward
        # to, independent of whichever path below resolves THIS page's own date.
        more_events = self._parse_more_events_widget(soup, page_date, venue)

        # 1) Lineup / series page: one observation per (performer, date) row.
        lineup = self._parse_lineup(soup, venue, page_year, url, title)
        if lineup:
            return lineup + more_events

        # 2) Single observation from the title (+ description fallback).
        if not title:
            return more_events
        time_start, time_end = parse_time(text)
        c = resolve_performer(title, description, page_date, page_year)

        # 3) Flyer-image fallback: some pages (e.g. a venue's "JULY LIVE
        # MUSIC" poster) publish the whole lineup as a single JPG with no
        # surrounding text at all, so title/description extraction never had
        # anything to find. Only worth the network + Vision-API round trip
        # once text extraction has already come up empty.
        if c["performer_status"] in ("unresolved", "category"):
            flyer_obs = self._parse_flyer_image(soup, venue, url, title)
            if flyer_obs:
                return flyer_obs + more_events

        return [self._assemble(
            performer=c["performer"], venue=venue, date=page_date,
            time_start=time_start, time_end=time_end, url=url,
            title_raw=title, description_raw=description,
            extraction_method=c["extraction_method"],
            performer_status=c["performer_status"],
            resolved=c["resolved"], event_category=c["event_category"],
        )] + more_events

    # -- flyer-image fallback (GPT-4o Vision) --------------------------------

    # SoWal's body-image template class for a full-width embedded photo/flyer.
    _FLYER_IMAGE_CLASS = "image-_bohr-body-image-full-width"

    @classmethod
    def _find_flyer_image_url(cls, soup) -> str | None:
        """Locate an embedded flyer/poster image in the page body, if any."""
        img = soup.find("img", class_=cls._FLYER_IMAGE_CLASS)
        if not img or not img.get("src"):
            return None
        return cls._absolutize(img["src"])

    def _download_flyer(self, img_url: str) -> Path | None:
        try:
            response = requests.get(img_url, headers=self.HEADERS, timeout=20)
            response.raise_for_status()
        except Exception as exc:
            logger.warning("[SoWalCrawler] Failed to fetch flyer image %s: %s", img_url, exc)
            return None

        ext = Path(img_url.split("?")[0]).suffix or ".jpg"
        fd, tmp_name = tempfile.mkstemp(suffix=ext)
        with os.fdopen(fd, "wb") as f:
            f.write(response.content)
        return Path(tmp_name)

    def _parse_flyer_image(self, soup, venue: str | None, url: str, title: str) -> list[dict]:
        """
        Run an embedded flyer image through the same GPT-4o Vision importer used
        for manually-dropped screenshots (app/images/importer.py), returning one
        observation per performer/date the model reads off the poster.

        Requires OPENAI_API_KEY; returns [] (no exception) when it's unset or
        the page has no such image, so callers fall back to the existing
        text-only "unresolved" observation exactly as before this fallback
        existed. No local-OCR path here — Apple Vision's parser (app/images/
        ocr.py) is positional/two-column, tuned for a different screenshot
        layout, and wouldn't make sense of a stylised calendar poster.
        """
        if not os.getenv("OPENAI_API_KEY"):
            return []

        img_url = self._find_flyer_image_url(soup)
        if not img_url:
            return []

        tmp_path = self._download_flyer(img_url)
        if not tmp_path:
            return []

        try:
            from app.images.importer import _call_gpt4o, _normalise
            raw = _call_gpt4o(tmp_path)
            normalised = _normalise(raw, tmp_path)
        except Exception as exc:
            logger.warning("[SoWalCrawler] Flyer image Vision extraction failed for %s: %s", img_url, exc)
            return []
        finally:
            tmp_path.unlink(missing_ok=True)

        if not normalised:
            return []

        logger.info(
            "[SoWalCrawler] Flyer image at %s yielded %d event(s) via GPT-4o Vision",
            img_url, len(normalised),
        )

        obs = []
        for ev in normalised:
            obs.append(self._assemble(
                performer=ev["performer"], venue=venue or ev["venue"], date=ev["date"],
                time_start=ev["time_start"], time_end=ev["time_end"], url=url,
                title_raw=title, description_raw=f"flyer image: {img_url}",
                extraction_method="flyer_image", performer_status="named",
                resolved=True, event_category="live_music",
            ))
        return obs

    @staticmethod
    def _in_calendar_widget(tag) -> bool:
        """
        True when `tag` sits inside SoWal's "Explore SoWal" recommendation
        widget (Drupal view ID 'date_calendar_lists', pane class
        'pane-date-calendar-lists-...') — a stack of single-row <table
        class="views-table"> elements listing OTHER events on the site (e.g.
        this same series' other upcoming dates), each with no date cell of
        its own. It was initially mistaken for a page-position thing (it
        renders in a "second column" wrapper), but that column also holds
        genuine event-detail panes on some pages -- position isn't a
        reliable signal, only the pane's own identity is. Scanning the whole
        page for <tr> (as a naive lineup parse would) misreads this widget as
        a multi-row lineup table and emits a garbage "unresolved, date=None"
        observation per widget row.
        """
        for ancestor in tag.parents:
            classes = ancestor.get("class") or []
            if any("view-date-calendar-lists" in c for c in classes):
                return True
        return False

    def _parse_lineup(
        self, soup, venue: str | None, page_year: int | None, url: str, title: str
    ) -> list[dict]:
        """Parse a table of (date, performer, time) rows into observations."""
        rows = []
        for tr in soup.find_all("tr"):
            if self._in_calendar_widget(tr):
                continue
            cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
            if len(cells) < 2:
                continue

            date_val = date_idx = None
            for idx, c in enumerate(cells):
                d = parse_lineup_date(c, page_year)
                if d:
                    date_val, date_idx = d, idx
                    break

            time_start = time_end = time_idx = None
            for idx, c in enumerate(cells):
                if idx == date_idx:
                    continue
                ts, te = _cell_time(c)
                if ts:
                    time_start, time_end, time_idx = ts, te, idx
                    break

            performer_cell = None
            for idx, c in enumerate(cells):
                if idx in (date_idx, time_idx) or not c.strip():
                    continue
                performer_cell = c.strip()
                break

            if performer_cell:
                rows.append((date_val, performer_cell, time_start, time_end))

        # Only treat the page as a lineup when several performer rows are present.
        if len(rows) < 2:
            return []

        obs = []
        for date_val, performer_cell, time_start, time_end in rows:
            # A cell like "featuring Zack Miller" still resolves to a name.
            name = extract_performer_from_description(performer_cell) or performer_cell.strip()

            unresolved = (
                not name
                or is_generic_title(name)
                or detect_category(name) is not None
                or date_val is None  # reject rows without a confident date
            )
            if unresolved:
                obs.append(self._assemble(
                    performer=None, venue=venue, date=date_val,
                    time_start=time_start, time_end=time_end, url=url,
                    title_raw=title, description_raw=performer_cell,
                    extraction_method="unresolved", performer_status="unresolved",
                    resolved=False, event_category="live_music",
                ))
            else:
                obs.append(self._assemble(
                    performer=name, venue=venue, date=date_val,
                    time_start=time_start, time_end=time_end, url=url,
                    title_raw=title, description_raw=performer_cell,
                    extraction_method="lineup", performer_status="named",
                    resolved=True, event_category="live_music",
                ))
        return obs

    @staticmethod
    def _extract_venue_and_description(soup, text: str, title: str) -> tuple[str | None, str]:
        """Venue from the title, overridden by an explicit 'Where:'; plus page description."""
        _perf, venue = split_title(title)
        m = re.search(r"Where:\s*(.+)", text)
        if m:
            venue = m.group(1).split("\n")[0].strip() or venue
        return venue, _page_description(soup)

    @staticmethod
    def _assemble(
        *, performer, venue, date, time_start, time_end, url,
        title_raw, description_raw, extraction_method, performer_status,
        resolved, event_category,
    ) -> dict:
        """Build one observation dict with full extraction evidence."""
        if performer:
            name = f"{performer} at {venue}" if venue else performer
        else:
            name = title_raw or venue or ""
        return {
            "name":             name,
            "performer":        performer,
            "venue":            venue,
            "date":             date,
            "time_start":       time_start,
            "time_end":         time_end,
            "stage":            None,
            "url":              url,
            "source":           "sowal",
            "observation_type": "website",
            # extraction evidence (kept on every emitted observation)
            "title_raw":        title_raw,
            "description_raw":  description_raw,
            "source_url":       url,
            "extraction_method": extraction_method,
            "performer_status": performer_status,
            "resolved":         resolved,
            "event_category":   event_category,
        }


def _page_description(soup) -> str:
    """Join the page's non-label paragraph text (excludes When/Time/Where lines)."""
    parts = []
    for p in soup.find_all("p"):
        txt = p.get_text(" ", strip=True)
        if not txt or re.match(r"^(when|time|where)\s*:", txt, re.I):
            continue
        parts.append(txt)
    return " ".join(parts)
