"""
app/favorites_watch/notify.py
Sends a push notification via ntfy.sh (https://ntfy.sh/docs/) -- a plain
HTTP POST to a topic URL, no account or API key required. The topic name
itself is the only "secret": anyone who knows it can read or publish to it,
so it's read from NTFY_TOPIC (an env var / repo secret) rather than
hardcoded, since this repo is public.
"""
import logging
import os

import requests

logger = logging.getLogger(__name__)


def send_notification(title: str, message: str) -> bool:
    topic = os.getenv("NTFY_TOPIC")
    if not topic:
        logger.warning("NTFY_TOPIC not set — skipping push notification (non-fatal)")
        return False
    try:
        resp = requests.post(
            f"https://ntfy.sh/{topic}",
            data=message.encode("utf-8"),
            headers={"Title": title, "Priority": "default", "Tags": "musical_note"},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        logger.warning("ntfy.sh notification failed: %s", exc)
        return False
