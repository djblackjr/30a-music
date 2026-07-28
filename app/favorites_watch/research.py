"""
app/favorites_watch/research.py
Calls the OpenAI Responses API (with the web_search tool) once per favorite
venue/artist to look for a specific, dated, sourced upcoming show -- the
same "Tier 1" bar used when this was done manually in chat: a real
calendar date and a real source URL, not a recurring weekly residency
description ("Thursdays 6-9PM") standing in for an actual booking.

Deliberately one small request per favorite rather than one big prompt
listing all of them: web_search tool calls degrade fast with too many
open-ended sub-questions in a single turn (confirmed during manual
research in chat -- broad multi-part queries returned mostly the same
generic aggregator pages for every subject). One focused question per
favorite costs more calls but far better precision.
"""
import json
import logging
import os
from datetime import date

logger = logging.getLogger(__name__)

MODEL = "gpt-4.1"

# gpt-4.1's training data extends well past this app's "today", so it must
# be told the real current date explicitly -- otherwise it has no way to
# know whether a date it finds is upcoming or already past (confirmed by an
# early test run returning a show from six days ago as "upcoming").
PROMPT_TEMPLATE = """Today's date is {today}. Search the web for ONE specific, \
dated, upcoming live-music event for "{name}" ({kind}) in the Walton County / \
30A / South Walton, Florida area, happening AFTER today's date and within the \
next 60 days.

Only report a finding if BOTH of these hold:
1. It names an actual calendar date (not "every Thursday" or "Sundays this \
summer" -- a recurring residency description is NOT a valid finding here).
2. You have a real, specific source URL for it (a venue's own site, 30a.com, \
SoWal.com, or similar -- not a generic homepage with no event-specific page).

If you can't find something meeting both bars, say so -- do not guess a date \
or invent a plausible-sounding one.

source_url MUST be an actual clickable URL starting with "http://" or \
"https://" that you can point to directly — never a description of a link \
or a citation-style reference. If you cannot produce a real URL, treat this \
the same as not finding anything and set found to false.

Respond with ONLY a JSON object, no markdown fences, no other text:
{{"found": true/false, "performer": "...", "venue": "...", "date": "YYYY-MM-DD", \
"time": "...", "source_url": "..."}}
If found is false, set the other fields to null."""


def _client():
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")
    return OpenAI(api_key=api_key)


def _parse_response(raw: str) -> dict | None:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("Unparseable research response: %r", raw[:200])
        return None
    if not data.get("found"):
        return None
    source_url = (data.get("source_url") or "").strip()
    # Confirmed live: the model sometimes ignores the prompt and returns a
    # prose description of a link ("link to event on 30a.com listing...")
    # instead of an actual URL. A truthiness check alone let that through,
    # so require the real http(s):// shape too.
    if not data.get("date") or not source_url.startswith(("http://", "https://")):
        logger.warning("Dropping finding missing date or a real source_url: %r", data)
        return None
    return data


def research_favorite(name: str, kind: str) -> dict | None:
    """kind is 'a venue' or 'an artist' -- fed straight into the prompt."""
    client = _client()
    prompt = PROMPT_TEMPLATE.format(today=date.today().isoformat(), name=name, kind=kind)
    try:
        resp = client.responses.create(model=MODEL, tools=[{"type": "web_search"}], input=prompt)
    except Exception as exc:
        logger.warning("Research call failed for %r: %s", name, exc)
        return None
    finding = _parse_response(resp.output_text)
    if finding:
        finding["favorite_name"] = name
    return finding


def research_all_favorites(venue_names: list[str], performer_names: list[str]) -> list[dict]:
    findings = []
    for name in venue_names:
        f = research_favorite(name, "a venue")
        if f:
            findings.append(f)
    for name in performer_names:
        f = research_favorite(name, "an artist")
        if f:
            findings.append(f)
    return findings
