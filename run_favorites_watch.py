#!/usr/bin/env python3
"""
run_favorites_watch.py
30A Music Intelligence — Favorites Watch

Researches each favorite venue/artist for a specific, dated, sourced
upcoming show (see app/favorites_watch/research.py), diffs the results
against the previous run's snapshot, and sends a single push notification
via ntfy.sh only when something genuinely new or changed turns up. Never
touches data/events.db or the public dashboard — see app/favorites_watch/
__init__.py for why this stays a separate side-channel.

Usage:
    python run_favorites_watch.py
"""
import json
import logging
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv optional; set OPENAI_API_KEY / NTFY_TOPIC manually if needed

LOG_DIR = Path("logs")
LOG_FILE = LOG_DIR / "favorites_watch.log"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
for lib in ("httpx", "openai", "urllib3", "httpcore"):
    logging.getLogger(lib).setLevel(logging.WARNING)

logger = logging.getLogger("favorites_watch")

STATE_PATH = Path("data/favorites_watch.json")

BANNER = """
\033[1;34m30A Music Intelligence — Favorites Watch\033[0m
\033[90m-----------------------------------------\033[0m
"""


def _load_state() -> list[dict]:
    if not STATE_PATH.exists():
        return []
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read previous state (%s) — treating as empty", exc)
        return []


def _save_state(findings: list[dict]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(findings, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _format_notification(changes: dict) -> tuple[str, str] | None:
    new, changed, removed = changes["new"], changes["changed"], changes["removed"]
    if not new and not changed:
        return None  # "removed" alone is a soft signal — see diff.py — not worth a push

    lines = []
    for f in new:
        lines.append(f"NEW: {f['performer'] or f['favorite_name']} @ {f['venue']} — {f['date']} {f['time'] or ''}".strip())
    for c in changed:
        before, after = c["before"], c["after"]
        lines.append(
            f"CHANGED: {after['favorite_name']} — was {before['date']} {before.get('venue', '')}, "
            f"now {after['date']} {after.get('venue', '')}".strip()
        )
    if removed:
        lines.append(f"(also: {len(removed)} previous finding(s) no longer showing up — could be stale, not necessarily cancelled)")

    title = f"30A Music: {len(new)} new, {len(changed)} changed"
    return title, "\n".join(lines)


def main() -> int:
    print(BANNER, end="")

    from app.dashboard.render import _favorite_performer_names, _favorite_venue_names
    from app.favorites_watch.diff import diff_findings
    from app.favorites_watch.notify import send_notification
    from app.favorites_watch.research import research_all_favorites

    venue_names = _favorite_venue_names()
    performer_names = _favorite_performer_names()
    print(f"  Researching {len(venue_names)} favorite venues + {len(performer_names)} favorite artists…\n")

    try:
        findings = research_all_favorites(venue_names, performer_names)
    except Exception as exc:
        logger.exception("Research failed: %s", exc)
        print(f"\n  \033[31m✗ Research failed: {exc}\033[0m\n")
        return 1

    previous = _load_state()
    changes = diff_findings(previous, findings)

    print(f"  \033[1mTier 1 findings this run:\033[0m {len(findings)}")
    print(f"  \033[32m✓ {len(changes['new'])} new\033[0m  "
          f"\033[33m~ {len(changes['changed'])} changed\033[0m  "
          f"\033[90m- {len(changes['removed'])} no longer found\033[0m\n")

    notification = _format_notification(changes)
    if notification:
        title, message = notification
        print(f"  \033[1mNotifying:\033[0m {title}")
        print(f"  {message}\n")
        if send_notification(title, message):
            print("  \033[32m✓ Push notification sent\033[0m\n")
        else:
            print("  \033[33m! Push notification not sent (see log)\033[0m\n")
    else:
        print("  \033[90mNo new/changed Tier 1 findings — no notification sent.\033[0m\n")

    _save_state(findings)
    print(f"  \033[90mState: {STATE_PATH}\033[0m")
    print(f"  \033[90mLog: {LOG_FILE}\033[0m\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
