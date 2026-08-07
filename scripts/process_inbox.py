#!/usr/bin/env python3
"""
scripts/process_inbox.py
Fired by a launchd WatchPaths agent (see scripts/com.30amusic.inbox-watcher.plist)
every time images/inbox/ changes, so a screenshot gets processed within
seconds of landing instead of waiting for the once-daily full pipeline.

Runs app.monitor.run_inbox_only(): image ingest (GPT-4o Vision) -> normalize
-> upsert -> the same conflict-resolution/recanonicalize/purge chain the full
pipeline uses -> dashboard + Excel regeneration. Does NOT touch git -- this
only updates the local db/docs/exports; publishing to the live site is still
the daily GitHub Actions job (or a manual `git push`).

A non-blocking file lock skips a run if one is already in flight (a burst of
several screenshots landing at once via AirDrop can fire the watcher more
than once): the in-flight run will pick up every file that's landed by the
time it starts, so a skipped duplicate firing costs nothing.
"""
import fcntl
import logging
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
        return 0
    except Exception:
        logger.exception("process_inbox run failed")
        return 1
    finally:
        fcntl.flock(lock_fp, fcntl.LOCK_UN)
        lock_fp.close()


if __name__ == "__main__":
    sys.exit(main())
