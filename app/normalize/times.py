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
