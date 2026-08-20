#!/usr/bin/env python3
"""
scripts/show_changes.py
Pretty-print a logs/changes/<run_id>.json changelog (see
app/reconcile/changelog.py) to the terminal -- a readable view of exactly
what changed in a run, since the raw JSON isn't a great read by hand.

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
CHANGES_DIR = REPO_ROOT / "logs" / "changes"

BOLD, DIM = "\033[1m", "\033[90m"
GREEN, YELLOW, RED = "\033[32m", "\033[33m", "\033[31m"
RESET = "\033[0m"


def _available() -> list[Path]:
    if not CHANGES_DIR.exists():
        return []
    return sorted(CHANGES_DIR.glob("*.json"))  # run_id is a timestamp -- lexical sort is chronological


def _event_line(ev: dict) -> str:
    artist = ev.get("performer") or "Unknown"
    venue = ev.get("venue") or "?"
    date = ev.get("date") or "?"
    time = ev.get("time_start") or ""
    return f"{artist} @ {venue}  {date} {time}".strip()


def print_changelog(path: Path) -> None:
    data = json.loads(path.read_text())
    summary = data.get("summary", {})

    print(f"\n{BOLD}Changelog: {path.name}{RESET}")
    print(f"{DIM}Recorded: {data.get('recorded_at', '?')}{RESET}")
    print(
        f"  {GREEN}+{summary.get('new', 0)} new{RESET}  "
        f"{YELLOW}~{summary.get('changed', 0)} changed{RESET}  "
        f"{RED}-{summary.get('removed', 0)} removed{RESET}  "
        f"{DIM}{summary.get('unchanged', 0)} unchanged{RESET}"
    )

    new = data.get("new", [])
    if new:
        print(f"\n  {BOLD}{GREEN}New ({len(new)}):{RESET}")
        for ev in new:
            print(f"    · {_event_line(ev)}")

    changed = data.get("changed", [])
    if changed:
        print(f"\n  {BOLD}{YELLOW}Changed ({len(changed)}):{RESET}")
        for ev in changed:
            print(f"    · {_event_line(ev)}")
            for field, d in ev.get("diff", {}).items():
                print(f"        {field}: {d['before']!r} -> {d['after']!r}")

    removed = data.get("removed", [])
    if removed:
        print(f"\n  {BOLD}{RED}Removed ({len(removed)}):{RESET}")
        for ev in removed:
            print(f"    · {_event_line(ev)}")

    print()


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

    print_changelog(target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
