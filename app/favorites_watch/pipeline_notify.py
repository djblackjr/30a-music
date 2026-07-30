"""
app/favorites_watch/pipeline_notify.py
Filters app.monitor.run_pipeline()'s own new/changed events down to the
ones that came from the favorites_watch crawler specifically (the AI
research over Tier 1 dated/sourced announcements -- see
app/crawlers/favorites_watch.py), and sends a single ntfy.sh push
notification summarizing them.

Deliberately NOT "any new/changed event at a favorite venue or by a
favorite performer, regardless of source": that was the first version of
this, and a live run confirmed it's the wrong scope -- SoWal/AJ's
Grayton/etc. surface dozens of ordinary rotating-lineup bookings a day at
popular favorite venues (Red Fish Taco, Shunk Gulley, ...), so that filter
produced a 165-event, 10KB notification on its very first real run --
which ntfy.sh silently turned into an unreadable file attachment rather
than a real push. The whole point of Favorites Watch was a small number of
specific, notable announcements, not every ordinary calendar row that
happens to match a venue name.
"""
import logging

from app.favorites_watch.notify import send_notification

logger = logging.getLogger(__name__)

SOURCE = "favorites_watch"

# Defensive cap even after the source filter above -- if a future change
# ever widens scope again, this keeps the notification body from silently
# degrading into an unreadable ntfy.sh file attachment (confirmed that's
# what happens past ~4KB) instead of failing loudly/visibly.
MAX_LINES = 20


def _describe(event: dict) -> str:
    bits = [event.get("performer"), "@", event.get("venue"), "—", event.get("date"), event.get("time_start") or ""]
    return " ".join(str(b) for b in bits if b).strip()


def _from_favorites_watch(event: dict) -> bool:
    return event.get("source") == SOURCE


def notify_favorites_changes(changes: dict) -> bool:
    """Returns True if a notification was actually sent (mirrors notify.send_notification)."""
    new_favs = [e for e in changes.get("new", []) if _from_favorites_watch(e)]
    changed_favs = [
        c for c in changes.get("changed", [])
        if _from_favorites_watch(c.get("after") or {})
    ]

    if not new_favs and not changed_favs:
        logger.info("No new/changed favorites_watch findings this run — no notification sent.")
        return False

    lines = [f"NEW: {_describe(e)}" for e in new_favs]
    lines += [f"CHANGED: {_describe(c['after'])}" for c in changed_favs]
    if len(lines) > MAX_LINES:
        omitted = len(lines) - MAX_LINES
        lines = lines[:MAX_LINES] + [f"...and {omitted} more (see the dashboard)"]

    title = f"30A Music: {len(new_favs)} new, {len(changed_favs)} changed (favorites)"
    sent = send_notification(title, "\n".join(lines))
    logger.info("Favorites notification %s: %s", "sent" if sent else "not sent", title)
    return sent
