"""
app/normalize/canonical.py
Canonical name/venue normalisation.

Ported verbatim from process_inbox.normalize_names — the hand-maintained table
of artist/venue spelling variants that should collapse to one canonical form.
Applied to both `performer` and `venue` fields, case-insensitively.
"""

# (canonical, variant) pairs. Every variant on the right maps to the canonical
# on the left. Ported from process_inbox.py; kept as domain data.
CANONICAL_FIXES: list[tuple[str, str]] = [
    ("The Typos", "THE TYPOS"),
    ("Stevie Monce", "STEVIE MONCE"),
    ("Casey Kearney", "CASEY KEARNEY"),
    ("Casey Kearney", "Casey Kearney Band"),
    ("Casey Kearney", "CASEY KEARNEY BAND"),
    ("Brett Stafford", "BRETT STAFFORD"),
    ("Brett Stafford", "Brett Stafford Smith"),
    ("Cadillac Willy", "CADILLAC WILLY"),
    # "Dion Jones" (solo) and "Dion Jones & The Neon Tears" (band) are DISTINCT
    # artists — do not collapse the band into the solo act. Normalise the
    # all-caps variant up to the full band name (choose the Neon Tears version).
    ("Dion Jones & The Neon Tears", "DION JONES & THE NEON TEARS"),
    ("Gage Cowart", "GAGE COWART"),
    ("Sunshine Wranglers", "SUNSHINE WRANLGERS"),
    ("Sunshine Wranglers", "The Sunshine Wranglers"),
    ("Boukou Groove", "BOUKOU GROOVE"),
    ("Harrison Prentice", "HARRISON PRENTICE"),
    ("Red Fish Taco", "RED FISH TACO"),
    ("Papa Surf", "Papa Surf Burger Bar"),
    ("Papa Surf", "PAPA SURF BURGER BAR"),
    ("Papa Surf", "Papa Surf Burger Bar 30A"),
    ("North Beach Social", "NORTH BEACH SOCIAL"),
    ("North Beach Social", "North Beach Social 1"),
    ("North Beach Social", "North Beach Social 2"),
    ("North Beach Social", "July Live Music Lineup"),
    ("North Beach Social", "july-live-music-lineup"),
    ("North Beach Social", "JULY LIVE MUSIC LINEUP"),
    ("30Avenue", "30AVENUE"),
    ("Queens Handsome", "QUEENS HANDSOME"),
    ("Lips Manly", "LIPS MANLY"),
    ("Zack Miller", "ZACK MILLER"),
    ("Pickled Pickers", "PICKLED PICKERS"),
    ("Nate Kelly", "NATE KELLY"),
    ("Zoe Walega", "ZOE WALEGA"),
    ("River Dan", "RIVER DAN"),
    ("Catalyst Dance Band", "CATALYST DANCE BAND"),
    ("Black Creek String Band", "BLACK CREEK STRING BAND"),
    ("Bill Garrett", "Bill Garrett + John Reinlie @ Brunch / Vine Illers"),
    ("Bill Garrett", "Bill Garrett + John Reinlie @ Brunch / Corey Hall Trio"),
    ("Bill Garrett", "Bill Garrett + John Reinlie @ Brunch / Harrison Prentice"),
    ("The Kennedys", "The Kennedy's"),
]

# variant (lowercased) -> canonical
_VARIANT_TO_CANONICAL: dict[str, str] = {
    variant.strip().lower(): canonical for canonical, variant in CANONICAL_FIXES
}


def canonicalize(value: str | None) -> str | None:
    """
    Return the canonical spelling for a performer/venue value.
    Unknown values pass through unchanged (only trimmed).
    """
    if not value:
        return value
    trimmed = value.strip()
    return _VARIANT_TO_CANONICAL.get(trimmed.lower(), trimmed)
