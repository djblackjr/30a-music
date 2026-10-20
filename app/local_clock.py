"""
app/local_clock.py
Wall-clock "today" for 30A, Walton County, FL (Central time) -- not whatever
clock the machine running this code happens to have.

GitHub Actions runners (and this app's own containers) run on UTC. A bare
datetime.now() returns that machine's clock, not Walton County's, so for the
~5-6 hours each evening between Central midnight and UTC midnight, naive
`datetime.now()` reports a date that is already tomorrow in UTC while it's
still tonight on the Emerald Coast -- confirmed live 2026-10-20: the pipeline
ran a few hours after UTC midnight while it was still 2026-10-19 evening in
Central time, and the dashboard's "Tonight" hero section showed events for
the 20th a full day early, and purge_past_events() was deleting "past" events
up to 5-6 hours before they were actually over locally.

Everywhere this pipeline needs "what calendar day/time is it right now" for a
human-facing purpose (the dashboard's Today/Tonight/This Week sections, the
TODAY_DATE_PLACEHOLDER header, the past-events purge cutoff) should go
through here instead of datetime.now().
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

# Walton County, FL -- the real timezone of every venue this app tracks,
# regardless of what timezone the generating machine is in.
LOCAL_TZ = ZoneInfo("America/Chicago")


def local_now() -> datetime:
    """Current wall-clock time in Central -- the actual 30A/Walton County timezone."""
    return datetime.now(LOCAL_TZ)


def local_today() -> str:
    """Current Central-time calendar date, as YYYY-MM-DD."""
    return local_now().strftime("%Y-%m-%d")


def local_date() -> date:
    """Current Central-time calendar date, as a `date` -- drop-in replacement
    for the naive, server-timezone `date.today()` wherever a real `date`
    object (not just the YYYY-MM-DD string local_today() gives) is needed."""
    return local_now().date()
