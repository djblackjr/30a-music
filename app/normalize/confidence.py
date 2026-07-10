"""
app/normalize/confidence.py
Confidence model — two dimensions per observation, aggregated per event.

Each OBSERVATION (one sighting of an event from one source) has:
  - source_confidence     — trust in the source itself
  - extraction_confidence — how well we read THIS observation (completeness +
                            any model-reported read confidence)
  - confidence            — effective per-observation score (source * extraction)

Each canonical EVENT aggregates its observations via ConfidenceAggregator into a
single confidence, plus source_count / verification_count / conflict metadata.
The aggregation ALGORITHM lives in ConfidenceAggregator so it can evolve without
touching the database schema.
"""
from datetime import datetime

# Trust in a source. image:* and ocr* matched by prefix in source_confidence().
SOURCE_TRUST: dict[str, float] = {
    "venue":   0.95,   # official venue websites (future)
    "sowal":   0.90,
    "crawler": 0.90,
    "seed":    0.60,
}
_DEFAULT_TRUST = 0.5

# Band thresholds (also used by the Excel exporter / dashboard).
HIGH_BAND = 0.80
MEDIUM_BAND = 0.50

# Confidence is never allowed to reach a certain 1.00.
CEILING = 0.99

_COMPLETENESS_FIELDS = ("date", "time_start", "venue", "performer")


def source_confidence(source: str | None) -> float:
    s = (source or "").strip().lower()
    if s.startswith("image:"):
        return 0.8
    if s.startswith("ocr"):
        return 0.5
    if s.startswith("venue"):
        return 0.95
    return SOURCE_TRUST.get(s, _DEFAULT_TRUST)


def _valid_date(value: str | None) -> bool:
    if not value:
        return False
    try:
        datetime.strptime(value.strip(), "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _completeness(ev: dict) -> float:
    present = 0
    if _valid_date(ev.get("date")):
        present += 1
    if (ev.get("time_start") or "").strip():
        present += 1
    if (ev.get("venue") or "").strip():
        present += 1
    if (ev.get("performer") or "").strip():
        present += 1
    return present / len(_COMPLETENESS_FIELDS)


def extraction_confidence(ev: dict) -> float:
    """
    How well this observation was read: a completeness heuristic, blended with
    the model's self-reported confidence (`model_confidence`) when present.
    """
    comp = 0.5 + 0.5 * _completeness(ev)
    model = ev.get("model_confidence")
    if isinstance(model, (int, float)):
        m = max(0.0, min(1.0, float(model)))
        return round(0.5 * comp + 0.5 * m, 3)
    return round(comp, 3)


def observation_confidence(ev: dict) -> float:
    """Effective per-observation confidence = source_confidence * extraction_confidence."""
    return round(source_confidence(ev.get("source")) * extraction_confidence(ev), 3)


def confidence_band(score: float | None) -> str:
    """Return 'high' | 'medium' | 'low' | 'unknown' for a score."""
    if score is None:
        return "unknown"
    if score >= HIGH_BAND:
        return "high"
    if score >= MEDIUM_BAND:
        return "medium"
    return "low"


def score_event(ev: dict) -> tuple[float, str]:
    """
    Convenience single-observation score + reason (effective confidence).
    Event-level confidence comes from ConfidenceAggregator, not this function.
    """
    sc = source_confidence(ev.get("source"))
    ec = extraction_confidence(ev)
    reason = f"source={ev.get('source') or 'unknown'} (trust {sc:.2f}); extraction {ec:.2f}"
    if isinstance(ev.get("model_confidence"), (int, float)):
        reason += f"; model {float(ev['model_confidence']):.2f}"
    return round(sc * ec, 3), reason


class ConfidenceAggregator:
    """
    Aggregate an event's confidence from its observations.

    Algorithm (hybrid; not max, not average, not literal noisy-OR):
      1. Start from the highest-confidence independent observation.
      2. Each additional AGREEING independent source raises confidence toward 1
         with diminishing returns (weighted, and (1-score) shrinks each step).
      3. A detected conflict applies a multiplicative penalty.
      4. Cap at CEILING (never 1.00).
      5. Extra agreeing sources only ever ADD a non-negative bonus, so a
         low-quality source can never reduce a high-confidence event — only a
         direct conflict reduces it.

    Independence: observations are de-duplicated by source (best per source), so
    two sightings from the same source don't double-count as corroboration.
    """

    CORROBORATION_WEIGHT = 0.5
    CONFLICT_PENALTY = 0.15
    CEILING = CEILING

    def aggregate(self, agreeing_observations: list[dict], has_conflict: bool = False) -> float:
        confs = self._independent_confidences(agreeing_observations)
        if not confs:
            return 0.0
        confs.sort(reverse=True)
        score = confs[0]
        for c in confs[1:]:
            score += (1.0 - score) * c * self.CORROBORATION_WEIGHT
        if has_conflict:
            score *= (1.0 - self.CONFLICT_PENALTY)
        return round(min(score, self.CEILING), 3)

    @staticmethod
    def _independent_confidences(observations: list[dict]) -> list[float]:
        best: dict[str, float] = {}
        for o in observations:
            src = o.get("source") or "unknown"
            c = o.get("confidence") or 0.0
            best[src] = max(best.get(src, 0.0), c)
        return list(best.values())
