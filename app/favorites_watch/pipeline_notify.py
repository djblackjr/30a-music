"""
app/favorites_watch/pipeline_notify.py
Filters app.monitor.run_pipeline()'s own new/changed events down to ones
involving a favorite venue or performer, and sends a single ntfy.sh push
notification summarizing them.

This reuses the pipeline's existing new/changed detection (identity-keyed
upsert in app/database/db.py) rather than keeping a second, separate
diffing mechanism -- there's only one source of truth for "did anything
change," and it applies to every event on the dashboard, not just the ones
a favorites-specific crawler happened to find this run. A favorite booking
that a *different* crawler (SoWal, AJ's Grayton, ...) surfaces first is
just as notification-worthy as one the favorites_watch crawler finds.
"""
import logging

from app.dashboard.render import _load_favorite_venues, _load_performer_meta, _performer_favorite, _venue_favorite
from app.favorites_watch.notify import send_notification

logger = logging.getLogger(__name__)


def _is_favorite_event(event: dict, venue_favs: set[str], performer_meta: dict) -> bool:
    return _venue_favorite(event.get("venue"), venue_favs) or _performer_favorite(event.get("performer"), performer_meta)


def _describe(event: dict) -> str:
    bits = [event.get("performer"), "@", event.get("venue"), "—", event.get("date"), event.get("time_start") or ""]
    return " ".join(str(b) for b in bits if b).strip()


def notify_favorites_changes(changes: dict) -> bool:
    """Returns True if a notification was actually sent (mirrors notify.send_notification)."""
    venue_favs = _load_favorite_venues()
    performer_meta = _load_performer_meta()

    new_favs = [e for e in changes.get("new", []) if _is_favorite_event(e, venue_favs, performer_meta)]
    changed_favs = [
        c for c in changes.get("changed", [])
        if _is_favorite_event(c.get("after") or {}, venue_favs, performer_meta)
    ]

    if not new_favs and not changed_favs:
        logger.info("No favorite-related new/changed events this run — no notification sent.")
        return False

    lines = [f"NEW: {_describe(e)}" for e in new_favs]
    lines += [f"CHANGED: {_describe(c['after'])}" for c in changed_favs]

    title = f"30A Music: {len(new_favs)} new, {len(changed_favs)} changed (favorites)"
    sent = send_notification(title, "\n".join(lines))
    logger.info("Favorites notification %s: %s", "sent" if sent else "not sent", title)
    return sent
