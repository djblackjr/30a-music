"""
app/reconcile/changelog.py
Persists a human-readable, per-run record of exactly which events changed --
new, changed (with a before/after field diff), and removed -- so that
question doesn't dead-end at the summary counts app.reconcile.changes logs
today. Nothing else in the pipeline keeps this detail: the `changes` dict
built in app/monitor.py never survives past the run that built it.

One file per run at logs/changes/<run_id>.json. A zero-delta run doesn't get
a file -- there's nothing to record, and it keeps the directory from filling
with empty markers.
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CHANGES_DIR = Path("logs") / "changes"

# Fields worth showing in a diff -- excludes internal bookkeeping columns
# (id, identity_key, run_id, conflict_flag/reason, date, name, performer,
# venue) that either never change post-identity or are already shown in the
# event's brief.
_DIFF_FIELDS = [
    "time_start", "time_end", "stage", "url",
    "confidence", "source", "source_count", "verification_count",
]


def _event_brief(ev: dict) -> dict:
    return {
        "performer":  ev.get("performer") or ev.get("name"),
        "venue":      ev.get("venue"),
        "date":       ev.get("date"),
        "time_start": ev.get("time_start"),
        "time_end":   ev.get("time_end"),
        "stage":      ev.get("stage"),
    }


def _diff(before: dict, after: dict) -> dict:
    diff = {}
    for field in _DIFF_FIELDS:
        b, a = before.get(field), after.get(field)
        if b != a:
            diff[field] = {"before": b, "after": a}
    return diff


def write_changelog(run_id: str, changes: dict, path: Path = CHANGES_DIR) -> Optional[Path]:
    """
    Write <path>/<run_id>.json with the full new/changed/removed event
    detail this run produced. Returns the path written, or None if there
    was nothing to record (a zero-delta run).
    """
    summary = changes.get("summary", {})
    if summary.get("total_delta", 0) == 0 and not changes.get("removed"):
        return None

    payload = {
        "run_id": run_id,
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
        "summary": summary,
        "new": [_event_brief(ev) for ev in changes.get("new", [])],
        "changed": [
            {
                **_event_brief(pair["after"]),
                "diff": _diff(pair["before"], pair["after"]),
            }
            for pair in changes.get("changed", [])
        ],
        "removed": [_event_brief(ev) for ev in changes.get("removed", [])],
    }

    path.mkdir(parents=True, exist_ok=True)
    out_path = path / f"{run_id}.json"
    out_path.write_text(json.dumps(payload, indent=2, default=str))
    logger.info("Wrote changelog: %s", out_path)
    return out_path
