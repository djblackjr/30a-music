"""
app/favorites_watch/
AI web-research for the favorite venues/artists in
app/dashboard/venue_groups.csv and app/dashboard/artists.csv: finds
specific, dated, sourced upcoming live-music announcements (never
recurring-residency guesses -- see research.py's prompt).

Fully integrated into the main pipeline, not a side channel:
  - research.py's findings are wrapped as a normal crawler
    (app/crawlers/favorites_watch.py) registered in
    app/crawlers/registry.py, so they flow through the same
    normalize/reconcile/confidence pipeline and data/events.db as every
    other source, and show up on the public dashboard like any other event.
  - pipeline_notify.py filters app.monitor.run_pipeline()'s own new/changed
    results down to events sourced from this crawler specifically (NOT
    "any event at a favorite venue, regardless of source" -- a live run
    confirmed that's far too broad, since SoWal/AJ's Grayton/etc. surface
    dozens of ordinary rotating bookings a day at popular favorite venues)
    and sends a single ntfy.sh push notification (notify.py). Still reuses
    the pipeline's own new/changed detection rather than a second diffing
    pass -- there is one mechanism for "did anything change," just filtered
    further for this purpose.
"""
