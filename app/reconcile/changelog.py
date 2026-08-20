"""
app/reconcile/changelog.py
Persists a human-readable, per-run record of exactly which events changed --
new, changed (with a before/after field diff), and removed -- so that
question doesn't dead-end at the summary counts app.reconcile.changes logs
today. Nothing else in the pipeline keeps this detail: the `changes` dict
built in app/monitor.py never survives past the run that built it.

Three files per run that actually changed something, all under
logs/changes/:
  - <run_id>.json   the structured record (what scripts/show_changes.py and
                     anything else that wants to parse this reads)
  - <run_id>.txt    the same content rendered as plain text -- open this one
                     directly in a text editor, no script required
  - latest.txt      overwritten every run, always the most recent one -- the
                     one file to keep open if you just want "what changed
                     last time" without hunting for a run_id

A zero-delta run writes none of these -- there's nothing to record, and it
keeps the directory (and latest.txt) from being clobbered with an empty
report.
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

# ANSI codes for scripts/show_changes.py's terminal view; render_text()
# leaves them out entirely (color=False) for the on-disk .txt files, so a
# plain text editor doesn't show raw escape codes.
_BOLD, _DIM = "\033[1m", "\033[90m"
_GREEN, _YELLOW, _RED = "\033[32m", "\033[33m", "\033[31m"
_RESET = "\033[0m"


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


def _event_line(ev: dict) -> str:
    artist = ev.get("performer") or "Unknown"
    venue  = ev.get("venue") or "?"
    date   = ev.get("date") or "?"
    time   = ev.get("time_start") or ""
    return f"{artist} @ {venue}  {date} {time}".strip()


def render_text(payload: dict, color: bool = False) -> str:
    """
    Render a changelog payload (the dict write_changelog() builds, or the
    same shape loaded back from a .json file) as readable text. Shared by
    write_changelog() (writes the plain .txt/latest.txt, color=False) and
    scripts/show_changes.py (prints the color version to the terminal) so
    the format only lives in one place.
    """
    def c(code: str, text: str) -> str:
        return f"{code}{text}{_RESET}" if color else text

    summary = payload.get("summary", {})
    lines = [
        c(_BOLD, f"Changelog: {payload.get('run_id', '?')}"),
        c(_DIM, f"Recorded: {payload.get('recorded_at', '?')}"),
        c(_GREEN, f"+{summary.get('new', 0)} new") + "  "
        + c(_YELLOW, f"~{summary.get('changed', 0)} changed") + "  "
        + c(_RED, f"-{summary.get('removed', 0)} removed") + "  "
        + c(_DIM, f"{summary.get('unchanged', 0)} unchanged"),
    ]

    new = payload.get("new", [])
    if new:
        lines.append("")
        lines.append(c(_BOLD + _GREEN, f"New ({len(new)}):"))
        lines += [f"  · {_event_line(ev)}" for ev in new]

    changed = payload.get("changed", [])
    if changed:
        lines.append("")
        lines.append(c(_BOLD + _YELLOW, f"Changed ({len(changed)}):"))
        for ev in changed:
            lines.append(f"  · {_event_line(ev)}")
            lines += [
                f"      {field}: {d['before']!r} -> {d['after']!r}"
                for field, d in ev.get("diff", {}).items()
            ]

    removed = payload.get("removed", [])
    if removed:
        lines.append("")
        lines.append(c(_BOLD + _RED, f"Removed ({len(removed)}):"))
        lines += [f"  · {_event_line(ev)}" for ev in removed]

    lines.append("")
    return "\n".join(lines)


def write_changelog(run_id: str, changes: dict, path: Path = CHANGES_DIR) -> Optional[Path]:
    """
    Write <path>/<run_id>.json, <path>/<run_id>.txt, and <path>/latest.txt
    with the full new/changed/removed event detail this run produced.
    Returns the .txt path written (the one meant to be opened directly), or
    None if there was nothing to record (a zero-delta run).
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
    json_path = path / f"{run_id}.json"
    json_path.write_text(json.dumps(payload, indent=2, default=str))

    text = render_text(payload, color=False)
    txt_path = path / f"{run_id}.txt"
    txt_path.write_text(text)
    (path / "latest.txt").write_text(text)

    logger.info("Wrote changelog: %s (+ %s, latest.txt)", json_path, txt_path.name)
    return txt_path
