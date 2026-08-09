"""
app/normalize/times.py
Time-string normalisation.

Ported from process_inbox.normalize_names:
  - convert 24-hour times to 12-hour (single times and ranges)
  - supply default times for venues that don't always post them

Deviation from the original: default times are applied ONLY when the event has
no usable time, rather than unconditionally overwriting (the original force-set
Shelby's regardless). This preserves any real time that was actually captured.
"""
import re

# Venues that reliably run a standard slot when no time is posted.
VENUE_DEFAULT_TIMES: dict[str, str] = {
    "Shelby's Beach Bar": "6:00 - 9:00 PM",
    "Papa Surf": "6:00 - 9:00 PM",
}

_MISSING = {"", "UNKNOWN", "TBD", "N/A", "NONE"}

_RANGE_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*[-–]\s*(\d{1,2}):(\d{2})\s*$")
_SINGLE_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")


def _to_12h(hour: int, minute: str) -> str:
    suffix = "AM" if hour < 12 else "PM"
    return f"{hour % 12 or 12}:{minute} {suffix}"


def normalize_time(value: str | None) -> str | None:
    """
    Convert a 24-hour time (single or range) to 12-hour format.
    Times already containing AM/PM, or that don't match, pass through unchanged.
    """
    if not value:
        return value
    t = value.strip()
    if "AM" in t.upper() or "PM" in t.upper():
        return t

    m = _RANGE_RE.match(t)
    if m:
        return (
            _to_12h(int(m.group(1)), m.group(2))
            + " - "
            + _to_12h(int(m.group(3)), m.group(4))
        )

    m = _SINGLE_RE.match(t)
    if m:
        return _to_12h(int(m.group(1)), m.group(2))

    return t


def apply_venue_default_time(venue: str | None, time_value: str | None) -> str | None:
    """
    If the venue has a known default slot and no usable time is present,
    return the default; otherwise return the time unchanged.
    """
    if not venue:
        return time_value
    default = VENUE_DEFAULT_TIMES.get(venue)
    if not default:
        return time_value
    if (time_value or "").strip().upper() in _MISSING:
        return default
    return time_value


# ---------------------------------------------------------------------------
# Display formatting: collapse every observed variant ("6PM", "6:00 pm",
# "6-9 PM", "5:00 PM - 8:00 PM CST", "6:30 pm CT", ...) to a single canonical
# "H:MM AM/PM" (or "H:MM AM/PM - H:MM AM/PM" for a range) -- rule set 2026-08-08.
# ---------------------------------------------------------------------------

# Trailing timezone abbreviation, if the source tacked one on. Every venue in
# this dataset is 30A/Walton County, FL -- Central time -- so the zone is
# redundant information once every time string is unambiguous, and stripping
# it is what lets the plain "H:MM AM/PM" format apply uniformly.
_TZ_SUFFIX_RE = re.compile(
    r"\s+(?:CT|CST|CDT|ET|EST|EDT|MT|MST|MDT|PT|PST|PDT)\s*$", re.IGNORECASE
)
# A range separator: hyphen/en-dash (with or without surrounding spaces, so
# both "6-9 PM" and "6:00 - 9:00 PM" split the same way) or the word "to"
# bounded by whitespace so it can't match inside another word.
_RANGE_SPLIT_RE = re.compile(r"\s*-\s*|\s*–\s*|\s+to\s+", re.IGNORECASE)
# A time WITH an explicit meridiem: "6", "6:00", optional space, then a/p
# and a required "m" (so "6a" alone doesn't count, matching the strictness
# of the dashboard's own client-side _fmtTime regex).
_TIME_WITH_MERIDIEM_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?$", re.IGNORECASE)
# A bare hour/minute with NO meridiem -- only valid as one side of a range
# whose other side supplies the meridiem (e.g. the "6" in "6-9 PM").
_BARE_TIME_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?$")


def _parse_time_token(token: str) -> tuple[int, int, str | None] | None:
    """(hour, minute, 'A'/'P'/None) for one side of a range, or None if unparseable."""
    t = token.strip()
    m = _TIME_WITH_MERIDIEM_RE.match(t)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2)) if m.group(2) else 0
        return (hour, minute, m.group(3).upper())
    m = _BARE_TIME_RE.match(t)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2)) if m.group(2) else 0
        return (hour, minute, None)
    return None


def _format_token(hour: int, minute: int, meridiem: str) -> str:
    return f"{hour}:{minute:02d} {'AM' if meridiem == 'A' else 'PM'}"


def split_time_range(value: str | None) -> tuple[str | None, str | None]:
    """
    Parse a time string that may be a single time or a start-end range into
    (formatted_start, formatted_end_or_None), each "H:MM AM/PM". A range
    with a meridiem on only one side (the overwhelmingly common flyer
    convention, e.g. "6-9 PM") inherits it on the other. Returns
    (value, None) unchanged whenever the string can't be confidently
    parsed -- e.g. neither side has a meridiem at all -- rather than
    mangling something this rule set doesn't understand.
    """
    if not value:
        return (value, None)
    s = _TZ_SUFFIX_RE.sub("", value.strip()).strip()
    if not s:
        return (value, None)

    parts = _RANGE_SPLIT_RE.split(s, maxsplit=1)
    parsed = [_parse_time_token(p) for p in parts]
    if any(p is None for p in parsed):
        return (value, None)

    if len(parsed) == 1:
        hour, minute, meridiem = parsed[0]
        if meridiem is None:
            return (value, None)
        return (_format_token(hour, minute, meridiem), None)

    (h1, m1, mer1), (h2, m2, mer2) = parsed
    mer1 = mer1 or mer2
    mer2 = mer2 or mer1
    if mer1 is None or mer2 is None:
        return (value, None)  # neither side had one -- nothing to inherit from
    return (_format_token(h1, m1, mer1), _format_token(h2, m2, mer2))


def format_time_range(value: str | None) -> str | None:
    """Single display string: 'H:MM AM/PM' or 'H:MM AM/PM - H:MM AM/PM'."""
    start, end = split_time_range(value)
    if end:
        return f"{start} - {end}"
    return start
