"""
app/favorites_watch/diff.py
Compares this run's findings against the previous run's snapshot. Keyed by
favorite_name (research.py asks one focused question per favorite, so each
favorite has at most one finding per run) -- a date/venue/time change under
the same favorite_name is a "changed" entry, not a remove+add, which is the
more useful signal (e.g. a show getting rescheduled).

"removed" (a favorite had a finding last run but not this run) is reported
separately and with lower confidence than new/changed: it just as often
means the AI search didn't resurface the same source this time as it means
the show was actually cancelled. Callers should treat it as a soft signal,
not an alert-worthy one.
"""

FIELDS_THAT_MATTER = ("performer", "venue", "date", "time", "source_url")


def _key(finding: dict) -> str:
    return finding["favorite_name"].strip().lower()


def diff_findings(old: list[dict], new: list[dict]) -> dict:
    old_by_key = {_key(f): f for f in old}
    new_by_key = {_key(f): f for f in new}

    added = [new_by_key[k] for k in new_by_key if k not in old_by_key]
    removed = [old_by_key[k] for k in old_by_key if k not in new_by_key]
    changed = [
        {"before": old_by_key[k], "after": new_by_key[k]}
        for k in new_by_key
        if k in old_by_key
        and any(old_by_key[k].get(f) != new_by_key[k].get(f) for f in FIELDS_THAT_MATTER)
    ]

    return {"new": added, "changed": changed, "removed": removed}
