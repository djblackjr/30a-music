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
from app.normalize.times import apply_venue_default_time, format_time_range, normalize_time, split_time_range

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


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def build_observation(raw: dict) -> dict | None:
    """
    Normalise one raw event into an observation (a sighting from one source),
    attaching source_confidence, extraction_confidence, effective confidence and
    a checksum. Returns None if it has no performer or no resolvable ISO date.
    """
    ev = dict(raw)

    performer = canonicalize((ev.get("performer") or "").strip())
    if not performer:
        return None

    # VISION_PROMPT explicitly allows date=null when a flyer's date isn't
    # determinable (e.g. "This Saturday" with no absolute date printed
    # anywhere -- confirmed live 2026-08-09, a Red Fish Taco flyer for "The
    # Typos Nate & Matt"). A dateless event is useless for a chronological
    # calendar and, worse, breaks the dashboard's date-grouping JS outright
    # (renders a bogus "undefined, undefined NaN" section header) -- so drop
    # it here rather than at display time, matching the missing-performer
    # guard above.
    if not _ISO_DATE_RE.match((ev.get("date") or "").strip()):
        return None

    venue = canonicalize((ev.get("venue") or "").strip()) or None
    ev["venue"] = venue

    # Some SoWal crawl paths capture "{performer} at {venue}" as the whole
    # performer field (the venue's own event-listing title) while other
    # passes for the SAME real event correctly separate performer from
    # venue -- without stripping this redundant suffix, the two produce
    # different identity_keys and duplicate the event on the dashboard
    # ("Wine & Song" vs "Wine & Song at NEAT" both landing as separate rows
    # for the same real weekly show).
    if venue:
        suffix = f" at {venue}"
        if performer.lower().endswith(suffix.lower()):
            performer = canonicalize(performer[: -len(suffix)].strip()) or performer

    # venue-aware alias (e.g. "Dion Jones" @ Stinky's -> the full band)
    performer = apply_venue_alias(performer, venue)
    ev["performer"] = performer

    ev["source"] = ev.get("source") or "crawler"
    ev["observation_type"] = ev.get("observation_type") or infer_observation_type(ev["source"])

    # normalize_time: 24h -> 12h. split_time_range: collapse every messy
    # 12h variant ("6PM", "6-9 PM", "6:30 pm CT", ...) to clean "H:MM AM/PM",
    # splitting an in-band range into (start, end) so time_end gets
    # populated even when the source crammed both into one string (see
    # times.py's module docstring for the full rule set, added 2026-08-08).
    time_val = normalize_time(ev.get("time_start") or ev.get("time"))
    time_val = apply_venue_default_time(venue, time_val)
    time_start, range_end = split_time_range(time_val)
    ev["time_start"] = time_start
    if range_end and not ev.get("time_end"):
        ev["time_end"] = range_end
    elif ev.get("time_end"):
        ev["time_end"] = format_time_range(ev["time_end"])

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

    if s.lower() == "noon":
        return 12 * 60
    if s.lower() == "midnight":
        return 0

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


# Mutable attributes that a lower-trust source can still fill in when the
# primary (highest-confidence) observation simply never asserted them --
# e.g. a manually-dropped screenshot supplies a `stage` the venue's own
# crawled listing never mentioned. Only a GAP gets filled: a field the
# primary source actually did provide is never overridden by a weaker one.
_FILLABLE_FIELDS = ("time_start", "time_end", "stage", "url")


def _coalesce_fields(observations: list[dict], primary: dict) -> dict:
    """For each fillable field, use primary's value, else the best other observation's."""
    ranked = sorted(observations, key=lambda o: o.get("confidence") or 0.0, reverse=True)
    resolved = {}
    for field in _FILLABLE_FIELDS:
        value = primary.get(field)
        if value in (None, ""):
            for o in ranked:
                candidate = o.get(field)
                if candidate not in (None, ""):
                    value = candidate
                    break
        resolved[field] = value
    return resolved


def aggregate_observations(observations: list[dict]) -> dict:
    """
    Aggregate ALL observations of one event (in-memory or loaded from the DB)
    into the canonical view: which observation leads, the aggregate confidence,
    source/verification counts, any conflict, and gap-filled mutable fields
    (see _coalesce_fields).

    Works on observation records, so it can be re-run whenever a new run adds an
    observation to an existing event (cross-run accumulation).

    Tie-break on confidence is observed_at, most recent wins -- same "freshest
    wins" policy as resolve_stale_url_relistings()/resolve_stale_image_relistings()
    for the identical scenario (same source re-describing a listing). Without
    this, a confidence tie falls back to whatever order SQLite happens to
    return event_observations rows in (no ORDER BY), which in practice meant
    an old observation saved before the 2026-08-08 time-format rules landed
    could keep winning primary over every freshly re-normalized observation
    of the same event forever, since its pre-fix checksum never matches a
    new one and it never ages out (confirmed live 2026-08-19: a stale
    "9:30AM"/"12:30 pm" observation from 2026-08-08 kept beating that day's
    correctly formatted "9:30 AM"/"12:30 PM" re-observation on every run).
    """
    primary = max(observations, key=lambda o: (o.get("confidence") or 0.0, o.get("observed_at") or ""))

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
        "resolved_fields":    _coalesce_fields(observations, primary),
    }


def merge_group(observations: list[dict]) -> dict:
    """
    Merge observations that share an identity into one canonical event.
    Field values come from the highest-confidence observation, except a gap
    in a mutable field (time/stage/url) can still be filled by a weaker
    source (see _coalesce_fields); confidence is aggregated; conflicts on
    time/stage are detected and penalised.
    """
    agg = aggregate_observations(observations)

    event = dict(agg["primary"])
    for k in ("confidence", "confidence_reason", "source_count",
              "verification_count", "conflict_flag", "conflict_reason"):
        event[k] = agg[k]
    event.update(agg["resolved_fields"])
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


# observation_type values that mean "read off an actual flyer/screenshot"
# (GPT-4o Vision or the Apple Vision OCR fallback) -- see infer_observation_type().
_FLYER_OBSERVATION_TYPES = {"image", "ocr"}


def _flyer_confidence(ev: dict) -> float:
    """
    Highest confidence among an event's flyer/screenshot-sourced observations,
    or -1.0 if it has none. Used by collapse_same_slot_duplicates() to prefer
    a flyer-backed variant -- "the venue's own flyer is the record of truth
    on conflict" is the policy this pipeline already applies elsewhere
    (resolve_stale_url_relistings/resolve_stale_image_relistings; also the
    reasoning behind the manual Papa Surf fix in commit 9c23041). -1.0 sorts
    below any real confidence, so max() falls through to first-seen when no
    variant has a flyer observation at all.
    """
    confidences = [
        obs.get("confidence") or 0.0
        for obs in ev.get("observations", [])
        if obs.get("observation_type") in _FLYER_OBSERVATION_TYPES
    ]
    return max(confidences) if confidences else -1.0


def collapse_same_slot_duplicates(events: list[dict]) -> list[dict]:
    """
    Collapse canonical events that are really the same booking listed under
    different venue text into one -- e.g. "The Typos" showed up as separate
    cards at "Red Fish Taco" and "Papa Surf" for the same date and 6:00 PM
    slot, because identity is performer + venue + date (see module
    docstring): inconsistent venue text across sources creates multiple
    distinct identities for one real show.

    Groups already-merged events by (performer, date, time_start) --
    deliberately ignoring venue -- and collapses any group of 2+ into one.

    Precedence: whichever variant has the highest-confidence flyer/screenshot
    observation wins (see _flyer_confidence) -- the venue's own flyer is the
    most reliable source for its own venue name, same policy this pipeline
    already applies to other same-source conflicts. If no variant has a
    flyer observation, the first-seen variant wins. Every collapsed
    variant's observations are merged onto the winner and source_count is
    recomputed over the merged set, so provenance isn't lost even though the
    losing venue text is.

    Deliberately narrow, so this can't quietly merge two real events: a
    different time for the same performer/date is a genuine double-booking
    (not collapsed), and different performers at the same venue/time are
    two different acts (not collapsed either) -- only an exact
    (performer, date, time_start) match collapses.
    """
    groups: dict[tuple[str, str, str], list[dict]] = {}
    order: list[tuple[str, str, str]] = []
    for ev in events:
        key = (
            (ev.get("performer") or "").strip().lower(),
            (ev.get("date") or "").strip(),
            (ev.get("time_start") or "").strip().lower(),
        )
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(ev)

    collapsed: list[dict] = []
    for key in order:
        variants = groups[key]
        if len(variants) == 1:
            collapsed.append(variants[0])
            continue

        # max() keeps the first-encountered item on a tie, so this also
        # covers the "no variant has a flyer observation" fallback (every
        # variant scores -1.0) and the "flyer confidences tie" case: both
        # resolve to first-seen, exactly the stated fallback precedence.
        winner = max(variants, key=_flyer_confidence)
        merged_observations = [obs for v in variants for obs in v.get("observations", [])]

        winner = dict(winner)
        winner["observations"] = merged_observations
        winner["source_count"] = len({obs.get("source") or "unknown" for obs in merged_observations})
        collapsed.append(winner)

    return collapsed


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

    merged = [merge_group(groups[key]) for key in order]
    return collapse_same_slot_duplicates(merged)
