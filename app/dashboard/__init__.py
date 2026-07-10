"""
app/dashboard/
Dumb dashboard generator: reads events + event_observations from the database
and renders docs/index.html. It NEVER computes confidence, reconciliation,
venue defaults, or canonical names — it only renders what the pipeline stored.
"""
