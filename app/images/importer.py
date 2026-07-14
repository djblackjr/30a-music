"""
app/images/importer.py
Image ingestion via GPT-4o Vision.

Drop PNG/JPG/JPEG files into images/inbox/ and this module will:
  1. Base64-encode each image
  2. Send it to GPT-4o Vision with a structured prompt
  3. Parse the returned JSON into normalised event dicts
  4. Move processed images to images/processed/

Requires:  OPENAI_API_KEY in .env (loaded by run_monitor.py)
Install:   pip install openai python-dotenv
"""
import base64
import json
import logging
import os
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

INBOX_DIR     = Path("images/inbox")
PROCESSED_DIR = Path("images/processed")
FAILED_DIR    = Path("images/failed")

SUPPORTED_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

# OpenAI Vision model — override with OPENAI_MODEL if needed.
VISION_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

VISION_PROMPT = """
You are a music event data extractor.

Analyze this image — it may be an Instagram schedule post, a venue entertainment calendar,
a flyer, or a screenshot from a venue website.

Extract EVERY live music event you can see. For each event return a JSON object with:
  - artist      (string, required) — performer name exactly as shown
  - venue       (string)           — venue name if visible, else null
  - date        (string)           — ISO 8601 date YYYY-MM-DD if determinable, else null
  - day_of_week (string)           — e.g. "Monday", "Friday" if shown without a full date
  - time_start  (string)           — e.g. "6PM", "8:30PM", null if not shown
  - stage       (string)           — e.g. "Main Stage", "Courtyard Stage", null if not shown
  - week_of     (string)           — if the schedule shows a week label like "6/22", include it
  - confidence  (number)           — your confidence from 0.0 to 1.0 that THIS event was
                                      read correctly (legible text, unambiguous fields)

Return ONLY a JSON array of event objects. No markdown, no explanation, no code fences.
If no events are found return an empty array: []

Today's date for reference: {today}
"""


def _encode_image(path: Path) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _mime_type(path: Path) -> str:
    ext = path.suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(ext, "image/jpeg")


def _call_gpt4o(image_path: Path) -> list[dict]:
    """Send image to GPT-4o Vision and return parsed event list."""
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("openai package not installed. Run: pip install openai")

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set. Add it to your .env file.")

    client = OpenAI(api_key=api_key)
    b64 = _encode_image(image_path)
    mime = _mime_type(image_path)
    prompt = VISION_PROMPT.format(today=date.today().isoformat())

    logger.info("Sending %s to OpenAI Vision (%s)...", image_path.name, VISION_MODEL)

    response = client.chat.completions.create(
        model=VISION_MODEL,
        max_tokens=2000,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime};base64,{b64}",
                            "detail": "high",
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )

    raw = response.choices[0].message.content.strip()

    # Strip accidental markdown fences
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return data
        logger.warning("GPT-4o returned non-list JSON for %s", image_path.name)
        return []
    except json.JSONDecodeError as exc:
        logger.error("JSON parse failed for %s: %s\nRaw: %s", image_path.name, exc, raw[:300])
        return []


def _coerce_confidence(value) -> Optional[float]:
    """Coerce a model-reported confidence into a clamped float, or None."""
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return None


def _normalise(raw_events: list[dict], image_path: Path) -> list[dict]:
    """
    Convert GPT-4o output into the standard event dict used by the pipeline.
    Resolves day_of_week + week_of into a real date where possible, and carries
    the model-reported confidence through as `model_confidence` so the central
    scorer (app/normalize/confidence.py) can blend it into the final score.
    """
    DAY_ORDER = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    normalised = []

    # Try to find a week anchor date from any event with week_of
    week_anchor: Optional[date] = None
    for ev in raw_events:
        wow = (ev.get("week_of") or "").strip()
        if wow and "/" in wow:
            try:
                m, d = wow.split("/")[:2]
                week_anchor = date(date.today().year, int(m), int(d))
            except Exception:
                pass
        if week_anchor:
            break

    for ev in raw_events:
        artist = (ev.get("artist") or "").strip()
        if not artist:
            continue

        venue     = (ev.get("venue") or "").strip() or None
        stage     = (ev.get("stage") or "").strip() or None
        time_start = (ev.get("time_start") or "").strip() or None
        event_date = (ev.get("date") or "").strip() or None
        dow        = (ev.get("day_of_week") or "").strip()

        # Resolve date from day_of_week + week_anchor
        if not event_date and dow and week_anchor:
            try:
                idx = DAY_ORDER.index(dow.title())
                # week_anchor is the Monday of that week
                anchor_dow = week_anchor.weekday()  # 0=Mon
                delta = idx - anchor_dow
                resolved = date(
                    week_anchor.year,
                    week_anchor.month,
                    week_anchor.day,
                )
                from datetime import timedelta
                resolved = resolved + timedelta(days=delta)
                event_date = resolved.isoformat()
            except (ValueError, Exception):
                pass

        name = f"{artist} at {venue}" if venue else artist

        normalised.append({
            "name":             name,
            "date":             event_date,
            "time_start":       time_start,
            "time_end":         None,
            "venue":            venue,
            "performer":        artist,
            "stage":            stage,
            "url":              None,
            "source":           f"image:{image_path.name}",
            "observation_type": "image",
            "model_confidence": _coerce_confidence(ev.get("confidence")),
        })

    return normalised


def process_inbox() -> list[dict]:
    """
    Process all images in images/inbox/.
    Returns normalised event list from all images combined.
    """
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    FAILED_DIR.mkdir(parents=True, exist_ok=True)

    images = [f for f in INBOX_DIR.iterdir() if f.suffix.lower() in SUPPORTED_EXTS]
    if not images:
        logger.info("No images found in %s", INBOX_DIR)
        return []

    logger.info("Found %d image(s) in inbox", len(images))
    all_events: list[dict] = []

    for img_path in images:
        try:
            raw = _call_gpt4o(img_path)
            events = _normalise(raw, img_path)
            logger.info(
                "%s → %d raw events → %d normalised",
                img_path.name, len(raw), len(events),
            )
            all_events.extend(events)
            shutil.move(str(img_path), str(PROCESSED_DIR / img_path.name))
        except Exception as exc:
            logger.error("Failed to process %s: %s", img_path.name, exc)
            shutil.move(str(img_path), str(FAILED_DIR / img_path.name))

    return all_events
