"""
app/normalize/provenance.py
Source provenance: turn raw events into observations, group observations by
event identity, detect conflicts, and merge each group into one canonical event
that carries its list of observations plus aggregate confidence.

Identity = performer + venue + date (the agreed model). Two sightings that share
identity are the same event; disagreement on a mutable field (time, stage) is a
CONFLICT, not two events.
"""
import hashlib

from app.normalize.canonical import canonicalize
from app.normalize.confidence import (
    ConfidenceAggregator,
    extraction_confidence,
    observation_confidence,
    source_confidence,
)
from app.normalize.times import apply_venue_default_time, normalize_time

_AGG = ConfidenceAggregator()


def infer_observation_type(source: str | None) -> str:
    """
    Infer how an observation was obtained from its source, when the producer did
    not declare an `observation_type` explicitly.
    One of: website / image / ocr / api / manual / social / calendar.
    """
    s = (source or "").strip().lower()
    if s.startswith("image:"):
        return "image"
    if s.startswith("ocr"):
        return "ocr"
    if s in ("instagram", "facebook"):
        return "social"
    if s == "seed":
        return "manual"
    return "website"


def _checksum(ev: dict) -> str:
    """Stable content hash of a normalized observation (hook for incremental crawl)."""
    parts = [
        (ev.get("performer") or "").lower(),
        (ev.get("venue") or "").lower(),
        (ev.get("date") or "").strip(),
        (ev.get("time_start") or "").strip().upper(),
        (ev.get("time_end") or "").strip().upper(),
        (ev.get("stage") or "").strip().lower(),
        (ev.get("source") or "").strip().lower(),
    ]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def build_observation(raw: dict) -> dict | None:
    """
    Normalise one raw event into an observation (a sighting from one source),
    attaching source_confidence, extraction_confidence, effective confidence and
    a checksum. Returns None if it has no performer.
    """
    ev = dict(raw)

    performer = canonicalize((ev.get("performer") or "").strip())
    if not performer:
        return None
    ev["performer"] = performer

    venue = canonicalize((ev.get("venue") or "").strip()) or None
    ev["venue"] = venue

    ev["source"] = ev.get("source") or "crawler"
    ev["observation_type"] = ev.get("observation_type") or infer_observation_type(ev["source"])

    time_val = normalize_time(ev.get("time_start") or ev.get("time"))
    time_val = apply_venue_default_time(venue, time_val)
    ev["time_start"] = time_val

    if not ev.get("name"):
        ev["name"] = f"{performer} at {venue}" if venue else performer

    ev["source_confidence"] = source_confidence(ev["source"])
    ev["extraction_confidence"] = extraction_confidence(ev)
    ev["confidence"] = observation_confidence(ev)
    ev["checksum"] = _checksum(ev)
    return ev


def _identity(ev: dict) -> str:
    return "|".join([
        (ev.get("performer") or "").lower(),
        (ev.get("venue") or "").lower(),
        (ev.get("date") or "").strip(),
    ])


def _observation_record(o: dict) -> dict:
    """The subset of an observation persisted to the event_observations table."""
    return {
        "source":                o.get("source"),
        "observation_type":      o.get("observation_type"),
        "url":                   o.get("url"),
        "source_confidence":     o.get("source_confidence"),
        "extraction_confidence": o.get("extraction_confidence"),
        "confidence":            o.get("confidence"),
        "checksum":              o.get("checksum"),
    }


def merge_group(observations: list[dict]) -> dict:
    """
    Merge observations that share an identity into one canonical event.
    Field values come from the highest-confidence observation; confidence is
    aggregated; conflicts on time/stage are detected and penalised.
    """
    primary = max(observations, key=lambda o: o.get("confidence") or 0.0)

    consensus_time  = (primary.get("time_start") or "").strip().upper()
    consensus_stage = (primary.get("stage") or "").strip().lower()

    conflicts = []
    for o in observations:
        ot = (o.get("time_start") or "").strip().upper()
        os_ = (o.get("stage") or "").strip().lower()
        if (ot and consensus_time and ot != consensus_time) or \
           (os_ and consensus_stage and os_ != consensus_stage):
            conflicts.append(o)

    has_conflict = bool(conflicts)
    agreeing = [o for o in observations if o not in conflicts]

    confidence = _AGG.aggregate(agreeing, has_conflict)

    sources_all   = {(o.get("source") or "unknown") for o in observations}
    sources_agree = {(o.get("source") or "unknown") for o in agreeing}

    conflict_reason = None
    if has_conflict:
        times = sorted({(o.get("time_start") or "").strip() for o in observations
                        if (o.get("time_start") or "").strip()})
        conflict_reason = "Time mismatch: " + " vs ".join(times) if len(times) > 1 else "Source conflict"

    event = dict(primary)
    event["confidence"]         = confidence
    event["source_count"]       = len(sources_all)
    event["verification_count"] = len(sources_agree)
    event["conflict_flag"]      = 1 if has_conflict else 0
    event["conflict_reason"]    = conflict_reason
    event["observations"]       = [_observation_record(o) for o in observations]

    if has_conflict:
        event["confidence_reason"] = f"{len(sources_all)} sources, conflict: {conflict_reason}"
    elif len(sources_agree) > 1:
        event["confidence_reason"] = f"{len(sources_agree)} sources agree"
    else:
        event["confidence_reason"] = f"single source ({primary.get('source')})"

    # Drop per-observation-only fields from the canonical event.
    for k in ("source_confidence", "extraction_confidence", "checksum",
              "model_confidence", "observation_type"):
        event.pop(k, None)

    return event


def normalize_and_group(events: list[dict]) -> list[dict]:
    """Raw events -> observations -> grouped -> canonical events (with observations)."""
    observations = [obs for obs in (build_observation(e) for e in events) if obs]

    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for o in observations:
        key = _identity(o)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(o)

    return [merge_group(groups[key]) for key in order]
