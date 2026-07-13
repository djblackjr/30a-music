"""
app/images/ocr.py
Optional OFFLINE screenshot ingestion via Apple Vision (macOS only).

Ported from the original ocr_and_rebuild.py. Free and local — no API key — but
markedly less accurate than the GPT-4o Vision importer, and macOS-only, so CI
can never use it. Used as the fallback when OPENAI_API_KEY is not set.

Assumes an ARTIST schedule screenshot (the layout the original script was built
for): the performer comes from the FILENAME, dates/times/venues are read out of
the image in two positional columns. A venue's own weekly lineup is handled far
better by app/images/importer.py (GPT-4o Vision).

Requires (macOS):  pip install pyobjc-framework-Vision
"""
import logging
import re
import shutil
from datetime import date, datetime
from pathlib import Path

from app.images.importer import FAILED_DIR, INBOX_DIR, PROCESSED_DIR, SUPPORTED_EXTS

logger = logging.getLogger(__name__)

# Venue keywords, in priority order — ported verbatim from ocr_and_rebuild.py.
_VENUE_MATCHERS: list[tuple[str, callable]] = [
    ("Chiringo",              lambda u: "CHIRINGO" in u),
    ("Red Fish Taco",         lambda u: "RED FISH" in u or u.startswith("ED FIS")),
    ("Papa Surf",             lambda u: "PAPA SURF" in u),
    ("Shelby's Beach Bar",    lambda u: "SHELBY" in u),
    ("The Big Chill",         lambda u: "BIG CHILL" in u),
    ("Watercolor Beach Club", lambda u: "WATERCOLOR" in u),
    ("Watersound Beach",      lambda u: "WATERSOUND" in u),
    ("Haughty Heron",         lambda u: "HAUGHTY" in u),
    ("Scallop Republic",      lambda u: "SCALLOP" in u),
    ("Stinky's Bait Shack",   lambda u: "STINKY" in u),
    ("Alibi Beach Lounge",    lambda u: "ALIBI" in u),
    ("Moe's BBQ",             lambda u: "MOES" in u or "MOE'S" in u),
    ("The Dock",              lambda u: "THE DOCK" in u),
    ("AJ's Grayton",          lambda u: "AJ" in u and "GRAYTON" in u),
    ("Shunk Gulley",          lambda u: "SHUNK" in u),
    ("Outcast",               lambda u: "OUTCAST" in u),
    ("Props Brewery SRB",     lambda u: "PROPS" in u),
]

_DATE_RE = re.compile(
    r"(?:MON|TUE|WED|THU|FRI|SAT|SUN)\w*\s+"
    r"(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\w*\s+(\d{1,2})"
)
_TIME_RE = re.compile(r"\b(\d{1,2}(?::\d{2})?\s*(?:AM|PM))\b")


def is_available() -> bool:
    """True only on macOS with pyobjc's Vision framework installed."""
    try:
        import Vision  # noqa: F401
        from Foundation import NSURL  # noqa: F401
        return True
    except ImportError:
        return False


def match_venue(text: str) -> str | None:
    """Map a line of OCR'd text to a known venue, or None."""
    upper = (text or "").upper()
    for venue, matches in _VENUE_MATCHERS:
        if matches(upper):
            return venue
    return None


def ocr_with_positions(image_path: Path) -> list[dict]:
    """Run Apple Vision text recognition, returning {x, y, text} for each observation."""
    import Vision
    from Foundation import NSURL

    url = NSURL.fileURLWithPath_(str(image_path.absolute()))
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, {})
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(1)  # accurate
    handler.performRequests_error_([request], None)

    items = []
    for obs in request.results() or []:
        box = obs.boundingBox()
        items.append({
            "x": box.origin.x,
            "y": box.origin.y,
            "text": obs.topCandidates_(1)[0].string(),
        })
    return items


def parse_two_column(items: list[dict], artist: str, year: int | None = None) -> list[dict]:
    """
    Rebuild events from positioned OCR text, reading the left and right columns
    top-to-bottom. A date sets the context; a venue follows; a time completes the
    event. Ported from ocr_and_rebuild.parse_two_column.
    """
    if year is None:
        year = date.today().year

    left  = sorted([i for i in items if i["x"] < 0.45],  key=lambda i: -i["y"])
    right = sorted([i for i in items if i["x"] >= 0.45], key=lambda i: -i["y"])

    events: list[dict] = []
    for column in (left, right):
        current_date = None
        current_venue = None
        for item in column:
            text = (item.get("text") or "").strip()
            if len(text) < 3:
                continue
            if any(c in text for c in ("ÿ", "¥", "¢", "%")):
                continue
            upper = text.upper()

            m = _DATE_RE.search(upper)
            if m:
                try:
                    month = m.group(1)[:3].title()
                    day = int(m.group(2))
                    current_date = datetime.strptime(f"{month} {day} {year}", "%b %d %Y").date()
                except ValueError:
                    pass
                current_venue = None
                continue

            m = _TIME_RE.search(upper)
            if m:
                current_time = m.group(1).strip()
                if current_date and current_venue:
                    events.append({
                        "name":       f"{artist} at {current_venue}",
                        "performer":  artist,
                        "venue":      current_venue,
                        "date":       current_date.isoformat(),
                        "time_start": current_time,
                        "time_end":   None,
                        "stage":      None,
                        "url":        None,
                    })
                current_venue = None
                continue

            venue = match_venue(upper)
            if venue:
                current_venue = venue

    return events


def process_inbox_ocr() -> list[dict]:
    """
    Read every image in images/inbox/ with Apple Vision and return event dicts.
    Processed images move to images/processed/, failures to images/failed/.
    Returns [] (with a warning) when Vision is unavailable — CI stays safe.
    """
    if not is_available():
        logger.warning(
            "Apple Vision unavailable (macOS + pyobjc-framework-Vision required); "
            "skipping OCR ingestion"
        )
        return []

    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    FAILED_DIR.mkdir(parents=True, exist_ok=True)

    images = [f for f in INBOX_DIR.iterdir() if f.suffix.lower() in SUPPORTED_EXTS]
    if not images:
        logger.info("No images found in %s", INBOX_DIR)
        return []

    all_events: list[dict] = []
    for img in images:
        try:
            artist = img.stem.replace("_", " ").title()
            items = ocr_with_positions(img)
            events = parse_two_column(items, artist)
            for ev in events:
                ev["source"] = f"ocr:{img.name}"
                ev["observation_type"] = "ocr"
            logger.info("%s → %d text items → %d events (OCR)", img.name, len(items), len(events))
            all_events.extend(events)
            shutil.move(str(img), str(PROCESSED_DIR / img.name))
        except Exception as exc:
            logger.error("OCR failed for %s: %s", img.name, exc)
            shutil.move(str(img), str(FAILED_DIR / img.name))

    return all_events
