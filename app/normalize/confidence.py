"""
app/normalize/confidence.py
Confidence-scoring framework.

Every event receives a score in [0.0, 1.0] describing how much to trust the
extraction, plus a human-readable reason. This is the AUTHORITATIVE live scorer
(the v2 DB migration only backfills legacy rows from source trust).

Score = source-trust base weight, adjusted by field completeness, and blended
with any model-reported confidence the importer supplies (`model_confidence`).
"""
from datetime import datetime

# Base trust by source. image:* and ocr* are matched by prefix in _base_trust.
SOURCE_TRUST: dict[str, float] = {
    "sowal": 0.9,
    "crawler": 0.9,
    "seed": 0.6,
}
_DEFAULT_TRUST = 0.5

# Band thresholds (also used by the Excel exporter / dashboard).
HIGH_BAND = 0.80
MEDIUM_BAND = 0.50

# Fields that contribute to completeness.
_COMPLETENESS_FIELDS = ("date", "time_start", "venue", "performer")


def _base_trust(source: str | None) -> float:
    s = (source or "").strip().lower()
    if s.startswith("image:"):
        return 0.8
    if s.startswith("ocr"):
        return 0.5
    return SOURCE_TRUST.get(s, _DEFAULT_TRUST)


def _valid_date(value: str | None) -> bool:
    if not value:
        return False
    try:
        datetime.strptime(value.strip(), "%Y-%m-%d")
        return True
    except ValueError:
        return False


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
    Compute (score, reason) for one event dict.

    - base = source trust
    - completeness scales the base between 0.5x (nothing) and 1.0x (all fields)
    - if the event carries a numeric `model_confidence`, blend it 50/50
    """
    base = _base_trust(ev.get("source"))

    present = 0
    if _valid_date(ev.get("date")):
        present += 1
    if (ev.get("time_start") or "").strip():
        present += 1
    if (ev.get("venue") or "").strip():
        present += 1
    if (ev.get("performer") or "").strip():
        present += 1
    total = len(_COMPLETENESS_FIELDS)
    completeness = present / total

    score = base * (0.5 + 0.5 * completeness)

    reason = (
        f"source={ev.get('source') or 'unknown'} (base {base:.2f}); "
        f"fields {present}/{total}"
    )

    model_conf = ev.get("model_confidence")
    if isinstance(model_conf, (int, float)):
        mc = max(0.0, min(1.0, float(model_conf)))
        score = 0.5 * score + 0.5 * mc
        reason += f"; model {mc:.2f}"

    score = round(max(0.0, min(1.0, score)), 3)
    return score, reason
