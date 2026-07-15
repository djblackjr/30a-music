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
import re
import time
from datetime import datetime

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
                if venue is None and enriched["venue"]:
                    venue = enriched["venue"]
                if performer_status in ("unresolved", "category"):
                    c = classify_performer(title, enriched["description"])
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

        # 1) Lineup / series page: one observation per (performer, date) row.
        lineup = self._parse_lineup(soup, venue, page_year, url, title)
        if lineup:
            return lineup

        # 2) Single observation from the title (+ description fallback).
        if not title:
            return []
        time_start, time_end = parse_time(text)
        c = classify_performer(title, description)
        return [self._assemble(
            performer=c["performer"], venue=venue, date=page_date,
            time_start=time_start, time_end=time_end, url=url,
            title_raw=title, description_raw=description,
            extraction_method=c["extraction_method"],
            performer_status=c["performer_status"],
            resolved=c["resolved"], event_category=c["event_category"],
        )]

    def _parse_lineup(
        self, soup, venue: str | None, page_year: int | None, url: str, title: str
    ) -> list[dict]:
        """Parse a table of (date, performer, time) rows into observations."""
        rows = []
        for tr in soup.find_all("tr"):
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
