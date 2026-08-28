#!/usr/bin/env python3
"""
scripts/process_inbox.py
Fired by a launchd WatchPaths agent (see scripts/com.30amusic.inbox-watcher.plist)
every time images/inbox/ changes, so a screenshot gets processed within
seconds of landing instead of waiting for the scheduled full pipeline.

Runs app.monitor.run_inbox_only(): image ingest (GPT-4o Vision) -> normalize
-> upsert -> the same conflict-resolution/recanonicalize/purge chain the full
pipeline uses -> dashboard + Excel regeneration. If that produced a new or
changed event, this also commits data/events.db + docs/index.html and pushes
-- the same two files the scheduled GitHub Actions job commits -- so a
screenshot landing in the inbox publishes to the live dashboard within
seconds instead of waiting for the scheduled run.

A non-blocking file lock skips a run if one is already in flight (a burst of
several screenshots landing at once via AirDrop can fire the watcher more
than once): the in-flight run will pick up every file that's landed by the
time it starts, so a skipped duplicate firing costs nothing.
"""
import fcntl
import logging
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCK_FILE = REPO_ROOT / "logs" / "process_inbox.lock"
LOG_FILE  = REPO_ROOT / "logs" / "process_inbox.log"

try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=REPO_ROOT / ".env")
except ImportError:
    pass  # dotenv optional; set OPENAI_API_KEY manually if needed

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("process_inbox")


def _publish() -> None:
    """
    Commit + push data/events.db, docs/index.html, and any new
    logs/changes/<run_id>.json, mirroring the commit step in
    .github/workflows/update-events.yml. No-ops quietly if there's nothing
    staged (e.g. the inbox run only touched exports/) or if the push fails
    (offline, conflict, etc.) -- a failed publish here just means the next
    scheduled run picks it up later, same as before this existed.
    """
    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(args, cwd=REPO_ROOT, capture_output=True, text=True)

    run("git", "config", "user.name", "30A Music Inbox Watcher")
    run("git", "config", "user.email", "actions@github.com")
    run("git", "add", "data/events.db", "docs/index.html", "logs/changes/")

    staged = run("git", "diff", "--staged", "--quiet")
    if staged.returncode == 0:
        logger.info("Nothing to publish (no changes to events.db/index.html)")
        return

    commit = run("git", "commit", "-m", "Auto-update events (inbox watcher)")
    if commit.returncode != 0:
        logger.error("git commit failed: %s", commit.stderr.strip())
        return

    push = run("git", "push")
    if push.returncode != 0:
        logger.error("git push failed: %s", push.stderr.strip())
        return

    logger.info("Published inbox update to origin/main")


def main() -> int:
    sys.path.insert(0, str(REPO_ROOT))
    import os
    os.chdir(REPO_ROOT)

    lock_fp = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        logger.info("Another process_inbox run is already in flight — skipping")
        return 0

    try:
        from app.monitor import run_inbox_only
        result = run_inbox_only()
        if result["image_files"] == 0:
            return 0
        logger.info("Done: %s", result)
        if result.get("new_or_changed", 0) > 0:
            _publish()
        else:
            logger.info("No new or changed events — skipping publish")
        return 0
    except Exception:
        logger.exception("process_inbox run failed")
        return 1
    finally:
        fcntl.flock(lock_fp, fcntl.LOCK_UN)
        lock_fp.close()


if __name__ == "__main__":
    sys.exit(main())
