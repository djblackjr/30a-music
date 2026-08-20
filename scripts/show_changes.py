#!/usr/bin/env python3
"""
scripts/show_changes.py
Print a logs/changes/<run_id>.json changelog (see app/reconcile/changelog.py)
to the terminal in color. Every run that changes something also writes a
plain-text version directly to disk now (<run_id>.txt and latest.txt,
alongside the .json) -- open those in a text editor if you don't want to run
a script at all. This is the same rendering, just with ANSI color for a
terminal.

Usage:
    python scripts/show_changes.py              # most recent changelog
    python scripts/show_changes.py <run_id>      # one specific run
    python scripts/show_changes.py --list        # list available run_ids
"""
import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.reconcile.changelog import render_text  # noqa: E402

CHANGES_DIR = REPO_ROOT / "logs" / "changes"


def _available() -> list[Path]:
    if not CHANGES_DIR.exists():
        return []
    # run_id is a timestamp -- lexical sort is chronological. latest.txt has
    # no .json counterpart and isn't a run in its own right, so it's excluded.
    return sorted(p for p in CHANGES_DIR.glob("*.json"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Show a per-run event changelog.")
    parser.add_argument("run_id", nargs="?", help="Specific run_id to show (default: most recent)")
    parser.add_argument("--list", action="store_true", help="List available run_ids and exit")
    args = parser.parse_args()

    available = _available()
    if not available:
        print(f"No changelogs found in {CHANGES_DIR}")
        return 0

    if args.list:
        for p in available:
            s = json.loads(p.read_text()).get("summary", {})
            print(
                f"{p.stem}  "
                f"({s.get('new', 0)} new, {s.get('changed', 0)} changed, {s.get('removed', 0)} removed)"
            )
        return 0

    if args.run_id:
        target = CHANGES_DIR / f"{args.run_id}.json"
        if not target.exists():
            print(f"No changelog for run_id {args.run_id!r} (try --list)")
            return 1
    else:
        target = available[-1]

    payload = json.loads(target.read_text())
    print(render_text(payload, color=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
