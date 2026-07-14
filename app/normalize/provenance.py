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
import re

from app.normalize.canonical import apply_venue_alias, canonicalize
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

    venue = canonicalize((ev.get("venue") or "").strip()) or None
    ev["venue"] = venue

    # venue-aware alias (e.g. "Dion Jones" @ Stinky's -> the full band)
    performer = apply_venue_alias(performer, venue)
    ev["performer"] = performer

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


def event_identity(ev: dict) -> str:
    """Stable identity key: performer + venue + date (the agreed model)."""
    return "|".join([
        (ev.get("performer") or "").strip().lower(),
        (ev.get("venue") or "").strip().lower(),
        (ev.get("date") or "").strip(),
    ])


# Backwards-compatible alias used internally.
_identity = event_identity


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
        # What THIS observation asserted — needed to detect conflicts between
        # observations recorded in different runs.
        "time_start":            o.get("time_start"),
        "time_end":              o.get("time_end"),
        "stage":                 o.get("stage"),
    }


_RANGE_RE = re.compile(
    r"^\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*[-–]\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*$", re.I
)
_SINGLE_RE = re.compile(r"^\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*$", re.I)


def _to_minutes(hour: str, minute: str | None, meridiem: str | None) -> int | None:
    h = int(hour)
    m = int(minute or 0)
    mer = (meridiem or "").lower()
    if mer == "pm" and h != 12:
        h += 12
    elif mer == "am" and h == 12:
        h = 0
    if h > 23 or m > 59:
        return None
    return h * 60 + m


def start_minutes(value: str | None) -> int | None:
    """
    Parse the START time of a time string into minutes past midnight, or None.

    Different sources format the same time differently ("6:00 - 9:00 PM" vs
    "6:00 pm"), so conflicts must be judged on the parsed time, not the raw text.
    For a range with only a trailing meridiem, the start's meridiem is inferred:
    "9:00 - 1:00 AM" starts at 9 PM, "6:00 - 9:00 PM" starts at 6 PM.
    """
    if not value:
        return None
    s = value.strip()

    m = _RANGE_RE.match(s)
    if m:
        a_h, a_m, a_mer, b_h, b_m, b_mer = m.groups()
        end_mer = b_mer or a_mer
        start_mer = a_mer
        if not start_mer and end_mer:
            if (int(a_h) % 12) <= (int(b_h) % 12):
                start_mer = end_mer
            else:  # range wraps past noon/midnight, e.g. 9:00 - 1:00 AM
                start_mer = "am" if end_mer.lower() == "pm" else "pm"
        return _to_minutes(a_h, a_m, start_mer)

    m = _SINGLE_RE.match(s)
    if m:
        return _to_minutes(m.group(1), m.group(2), m.group(3))

    return None


def _times_conflict(a: str | None, b: str | None) -> bool:
    """Two times conflict only if BOTH parse and land on different start times."""
    ma, mb = start_minutes(a), start_minutes(b)
    if ma is None or mb is None:
        return False
    return ma != mb


def aggregate_observations(observations: list[dict]) -> dict:
    """
    Aggregate ALL observations of one event (in-memory or loaded from the DB)
    into the canonical view: which observation leads, the aggregate confidence,
    source/verification counts, and any conflict.

    Works on observation records, so it can be re-run whenever a new run adds an
    observation to an existing event (cross-run accumulation).
    """
    primary = max(observations, key=lambda o: o.get("confidence") or 0.0)

    consensus_time  = primary.get("time_start")
    consensus_stage = (primary.get("stage") or "").strip().lower()

    conflicts = []
    for o in observations:
        os_ = (o.get("stage") or "").strip().lower()
        if _times_conflict(o.get("time_start"), consensus_time) or \
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
                        if start_minutes(o.get("time_start")) is not None},
                       key=lambda t: start_minutes(t))
        conflict_reason = "Time mismatch: " + " vs ".join(times) if len(times) > 1 else "Source conflict"

    if has_conflict:
        reason = f"{len(sources_all)} sources, conflict: {conflict_reason}"
    elif len(sources_agree) > 1:
        reason = f"{len(sources_agree)} sources agree"
    else:
        reason = f"single source ({primary.get('source')})"

    return {
        "primary":            primary,
        "confidence":         confidence,
        "confidence_reason":  reason,
        "source_count":       len(sources_all),
        "verification_count": len(sources_agree),
        "conflict_flag":      1 if has_conflict else 0,
        "conflict_reason":    conflict_reason,
    }


def merge_group(observations: list[dict]) -> dict:
    """
    Merge observations that share an identity into one canonical event.
    Field values come from the highest-confidence observation; confidence is
    aggregated; conflicts on time/stage are detected and penalised.
    """
    agg = aggregate_observations(observations)

    event = dict(agg["primary"])
    for k in ("confidence", "confidence_reason", "source_count",
              "verification_count", "conflict_flag", "conflict_reason"):
        event[k] = agg[k]
    event["observations"] = [_observation_record(o) for o in observations]

    # Drop per-observation-only fields from the canonical event.
    for k in ("source_confidence", "extraction_confidence", "checksum",
              "model_confidence", "observation_type",
              # SoWal extraction evidence — used to weight extraction_confidence
              # in build_observation(); not part of the canonical event shape.
              "title_raw", "description_raw", "source_url",
              "extraction_method", "performer_status", "resolved", "event_category"):
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
