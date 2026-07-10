"""
app/normalize/
Single normalisation pass for the pipeline. Every event from every source flows
through normalize_events() before it is saved or reconciled.

Consolidates rules that used to be scattered across:
  - app/monitor._normalise_events   (dedup, name fill, drop empty performer)
  - process_inbox.normalize_names    (name canonicalisation, time formatting,
                                       venue default times, trimming)

and attaches a confidence score to every event.
"""
import logging

from app.normalize.canonical import canonicalize
from app.normalize.confidence import confidence_band, score_event
from app.normalize.times import apply_venue_default_time, normalize_time

logger = logging.getLogger(__name__)

__all__ = ["normalize_events", "canonicalize", "score_event", "confidence_band"]


def normalize_events(events: list[dict]) -> list[dict]:
    """
    Normalise and score a list of raw event dicts.

    Steps, per event:
      1. drop events with no performer
      2. trim + canonicalise performer and venue
      3. default source to 'crawler' when missing
      4. normalise time (24h -> 12h) and apply venue default times
      5. fill display name if absent
      6. deduplicate on performer|venue|date|time (exact duplicates only)
      7. attach confidence + confidence_reason

    Note: intra-run dedup keeps time in the key so two genuinely different-time
    entries survive; reconciliation identity (performer|venue|date) is coarser
    by design — see app/reconcile/changes.py.
    """
    seen: set[str] = set()
    out: list[dict] = []

    for raw in events:
        ev = dict(raw)  # never mutate the caller's dict

        performer = canonicalize((ev.get("performer") or "").strip())
        if not performer:
            continue
        ev["performer"] = performer

        venue = canonicalize((ev.get("venue") or "").strip()) or None
        ev["venue"] = venue

        ev["source"] = ev.get("source") or "crawler"

        time_val = normalize_time(ev.get("time_start") or ev.get("time"))
        time_val = apply_venue_default_time(venue, time_val)
        ev["time_start"] = time_val

        if not ev.get("name"):
            ev["name"] = f"{performer} at {venue}" if venue else performer

        key = "|".join([
            performer.lower(),
            (venue or "").lower(),
            (ev.get("date") or "").strip(),
            (time_val or "").strip().upper(),
        ])
        if key in seen:
            continue
        seen.add(key)

        score, reason = score_event(ev)
        ev["confidence"] = score
        ev["confidence_reason"] = reason

        out.append(ev)

    logger.info("Normalised %d raw -> %d events", len(events), len(out))
    return out
