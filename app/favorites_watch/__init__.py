"""
app/favorites_watch/
Daily AI web-research check for the favorite venues/artists in
app/dashboard/venue_groups.csv and app/dashboard/artists.csv: finds
specific, dated, sourced upcoming live-music announcements (never
recurring-residency guesses -- see research.py's prompt) and diffs them
against the previous run's snapshot (data/favorites_watch.json) so a push
notification only fires on a genuine new/changed finding, not every run.

This is intentionally separate from app/monitor.py's crawler pipeline: it
never writes to data/events.db or the public dashboard. It is a personal
heads-up channel, not a data source for the site.
"""
