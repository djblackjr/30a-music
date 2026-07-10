"""
app/normalize/
Single normalisation pass for the pipeline. Every event from every source flows
through normalize_events() before it is saved or reconciled.

Consolidates rules that used to be scattered across:
  - app/monitor._normalise_events   (dedup, name fill, drop empty performer)
  - process_inbox.normalize_names    (name canonicalisation, time formatting,
                                       venue default times, trimming)

and, as of Phase 3C, groups multiple sightings of an event into ONE canonical
event backed by many observations (source provenance), with an aggregate
confidence and conflict detection.
"""
import logging

from app.normalize.canonical import canonicalize
from app.normalize.confidence import ConfidenceAggregator, confidence_band, score_event
from app.normalize.provenance import build_observation, normalize_and_group

logger = logging.getLogger(__name__)

__all__ = [
    "normalize_events",
    "canonicalize",
    "score_event",
    "confidence_band",
    "ConfidenceAggregator",
    "build_observation",
]


def normalize_events(events: list[dict]) -> list[dict]:
    """
    Normalise raw events and group them by identity (performer + venue + date)
    into canonical events. Each returned event carries:
      - merged field values (from its highest-confidence observation)
      - an aggregate `confidence` + `confidence_reason`
      - `source_count`, `verification_count`, `conflict_flag`, `conflict_reason`
      - an `observations` list (persisted to the event_observations table)
    """
    out = normalize_and_group(events)
    logger.info("Normalised %d raw -> %d canonical events", len(events), len(out))
    return out
