#!/usr/bin/env python3
"""
run_monitor.py
30A Music Intelligence — Run Monitor

Usage:
    python run_monitor.py

Loads .env, runs the full pipeline, prints a clean summary.
"""
import logging
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Load environment variables from .env BEFORE importing anything else
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv optional; set OPENAI_API_KEY manually if needed

# ---------------------------------------------------------------------------
# Logging setup — file + console (WARNING only on console to keep output clean)
# ---------------------------------------------------------------------------
LOG_DIR  = Path("logs")
LOG_FILE = LOG_DIR / "run_monitor.log"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),   # remove this line to silence console logs
    ],
)
# Silence noisy third-party loggers
for lib in ("httpx", "openai", "urllib3", "httpcore"):
    logging.getLogger(lib).setLevel(logging.WARNING)

logger = logging.getLogger("run_monitor")

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
BANNER = """
\033[1;34m30A Music Intelligence — Run Monitor\033[0m
\033[90m------------------------------------\033[0m
"""


def print_summary(result: dict) -> None:
    changes  = result.get("changes", {})
    summary  = changes.get("summary", {})

    new_count     = summary.get("new", 0)
    changed_count = summary.get("changed", 0)
    removed_count = summary.get("removed", 0)

    print(BANNER)
    print(f"  \033[1mCrawler events found:\033[0m   {result['crawler_events']}")
    print(f"  \033[1mImage files found:\033[0m      {result['image_files']}")
    print(f"  \033[1mEvents saved:\033[0m           {result['events_saved']}")
    print(f"  \033[1mPast events purged:\033[0m     {result['purged_past']}")
    print(f"  \033[1mNew or changed events:\033[0m  {result['new_or_changed']}")
    print(f"  \033[1mExcel report:\033[0m           {result['report_path']}")
    print()

    if result["new_or_changed"] > 0:
        print(f"  \033[32m✓ {new_count} new\033[0m  "
              f"\033[33m~ {changed_count} changed\033[0m  "
              f"\033[31m✗ {removed_count} removed\033[0m")

        new_events = changes.get("new", [])[:5]
        if new_events:
            print()
            print("  \033[1mNew events (up to 5):\033[0m")
            for ev in new_events:
                artist = ev.get("performer") or ev.get("name") or "Unknown"
                venue  = ev.get("venue") or "?"
                date   = ev.get("date") or "?"
                time   = ev.get("time_start") or ""
                print(f"    · {artist} @ {venue}  {date} {time}")
    else:
        print("  \033[90mNo changes since last run.\033[0m")

    print()
    print(f"  \033[90mLog: {LOG_FILE}\033[0m")
    print()


def main() -> int:
    print(BANNER, end="")
    print("  Running pipeline…\n")

    try:
        from app.monitor import run_pipeline
        result = run_pipeline()
    except Exception as exc:
        logger.exception("Pipeline failed: %s", exc)
        print(f"\n  \033[31m✗ Pipeline failed: {exc}\033[0m")
        print(f"  See {LOG_FILE} for details.\n")
        return 1

    print_summary(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
