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
    results down to favorite venues/performers and sends a single ntfy.sh
    push notification (notify.py) -- there is one new/changed detection
    mechanism for the whole app, not a separate one just for favorites.
"""
