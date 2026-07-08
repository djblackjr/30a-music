"""
app/reconcile/changes.py
Compare current run events to the previous run and classify changes.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _event_key(ev: dict) -> str:
    """
    Stable identity key — performer + date + time.
    Venue is intentionally excluded so a venue change is detected as a 'changed'
    event rather than a remove + new.
    """
    performer = (ev.get("performer") or "").strip().lower()
    date      = (ev.get("date") or "").strip()
    time      = (ev.get("time_start") or "").strip().upper()
    return f"{performer}|{date}|{time}"


def _event_signature(ev: dict) -> str:
    """Full content hash — used to detect changes to existing events."""
    fields = [
        ev.get("name") or "",
        ev.get("date") or "",
        ev.get("time_start") or "",
        ev.get("time_end") or "",
        ev.get("venue") or "",
        ev.get("performer") or "",
        ev.get("stage") or "",
    ]
    return "|".join(f.strip().lower() for f in fields)


def compare_runs(
    current_events: list[dict],
    previous_events: list[dict],
) -> dict:
    """
    Compare two event lists and return a summary dict:
      - new:      events not in previous run
      - changed:  events whose details changed since last run
      - removed:  events in previous run not in current run
      - unchanged: events identical in both runs
      - summary:  counts
    """
    prev_by_key = {_event_key(e): e for e in previous_events}
    curr_by_key = {_event_key(e): e for e in current_events}

    new_events      = []
    changed_events  = []
    unchanged_events = []

    for key, ev in curr_by_key.items():
        if key not in prev_by_key:
            new_events.append(ev)
        elif _event_signature(ev) != _event_signature(prev_by_key[key]):
            changed_events.append({
                "before": prev_by_key[key],
                "after":  ev,
            })
        else:
            unchanged_events.append(ev)

    removed_events = [
        ev for key, ev in prev_by_key.items()
        if key not in curr_by_key
    ]

    total_delta = len(new_events) + len(changed_events)
    logger.info(
        "Changes — new: %d | changed: %d | removed: %d | unchanged: %d",
        len(new_events), len(changed_events), len(removed_events), len(unchanged_events),
    )

    return {
        "new":       new_events,
        "changed":   changed_events,
        "removed":   removed_events,
        "unchanged": unchanged_events,
        "summary": {
            "new":       len(new_events),
            "changed":   len(changed_events),
            "removed":   len(removed_events),
            "unchanged": len(unchanged_events),
            "total_delta": total_delta,
        },
    }
