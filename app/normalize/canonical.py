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
    # Favorite is listed as "Will Thompson" in artists.csv; favorite-matching
    # is an exact string match with no fuzzy logic, so the "Band" suffix
    # broke the star (confirmed live 2026-07-22).
    ("Will Thompson", "Will Thompson Band"),
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
    # "Boucou Groove" was a GPT-4o Vision misread of a flyer (confirmed
    # 2026-08-06): sowal's own North Beach Social booking widget has the
    # correctly-spelled "Boukou Groove" for the same night, and this venue
    # never double-books its single 6-9pm slot.
    ("Boukou Groove", "Boucou Groove"),
    ("Harrison Prentice", "HARRISON PRENTICE"),
    ("Red Fish Taco", "RED FISH TACO"),
    # GPT-4o Vision isn't deterministic call-to-call: re-processing the exact
    # same screenshot on a different day produced "Papa Surf" one run and the
    # bare Instagram handle "papasurfburgerbar" the next (see VISION_PROMPT's
    # "use the Instagram username" fallback in app/images/importer.py) --
    # same real venue, so both must collapse to one identity.
    ("Red Fish Taco", "redfishtaco"),
    ("Papa Surf", "Papa Surf Burger Bar"),
    ("Papa Surf", "PAPA SURF BURGER BAR"),
    ("Papa Surf", "Papa Surf Burger Bar 30A"),
    ("Papa Surf", "papasurfburgerbar"),
    ("Papa Surf", "PapaSurf Burger Bar"),
    ("Papa Surf", "PAPASURF BURGER BAR"),
    ("Papa Surf", "Papa Surf's, Santa Rosa"),
    # A no-space CamelCase read of a monthly lineup flyer (confirmed live
    # 2026-08-08) -- same VISION_PROMPT quirk as papasurfburgerbar above,
    # different flavor. Left un-canonicalized, this also silently broke
    # apply_venue_default_time()'s exact-match lookup (times.py), which is
    # why several of these landed with no time at all -- see
    # backfill_venue_default_times() in app/database/db.py.
    ("Papa Surf", "PapaSurf"),
    # Same "use the Instagram username shown in the screenshot's UI chrome"
    # fallback, but this time the account in the screenshot was the
    # performer reposting the venue's announcement, not the venue's own
    # account -- confirmed live 2026-08-08, a Stevie Monce repost of Papa
    # Surf's own booking got the venue read as "steviemonce" instead.
    ("Papa Surf", "steviemonce"),
    ("Shelby's Beach Bar", "shelbysbeachbar"),
    # GPT-4o Vision reads the venue's fuller on-flyer name; same real venue.
    ("Shelby's Beach Bar", "Shelby's Beach Bar and Grill"),
    # favorites_watch's OpenAI web_search reported the "&" spelling of the
    # same fuller name -- confirmed live 2026-07-30, this venue is in
    # venue_groups.csv as "Shelby's Beach Bar" so the "& Grill" variant was
    # silently missing its favorite star.
    ("Shelby's Beach Bar", "Shelby's Beach Bar & Grill"),
    # Same "read the Instagram handle off the flyer" pattern as papasurfburgerbar
    # above -- confirmed live 2026-07-30 on a Stevie Monce flyer reposted from
    # @chiringograyton.
    ("Chiringo", "chiringograyton"),
    ("North Beach Social", "NORTH BEACH SOCIAL"),
    ("North Beach Social", "northbeachsocial"),
    ("North Beach Social", "North Beach Social 1"),
    ("North Beach Social", "North Beach Social 2"),
    ("North Beach Social", "July Live Music Lineup"),
    ("North Beach Social", "july-live-music-lineup"),
    ("North Beach Social", "JULY LIVE MUSIC LINEUP"),
    ("North Beach Social", "North Beach Social, Santa Rosa"),
    ("North Beach Social", "North Beach Social (Santa Rosa Beach, FL)"),
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

    # Venue spelling/formatting variants surfaced by the SoWal crawler port
    # (raw text straight off sowal.com; verified against a live crawl,
    # 2026-07-13). Confirmed same physical venue — not fuzzy-matched.
    ("AJ's Grayton Beach", "AJ's Grayton"),
    ("Aaron Bessant Park", "Aaron Bessant Park at Pier Park"),
    ("Crackings", "Crackings. - Grayton Beach"),
    ("Hilton Sandestin Beach Golf Resort & Spa", "Hilton Sandestin Golf Resort & Spa"),
    ("Seascape Towne Centre", "Seascape Town Centre"),
    ("Seaside Amphitheater", "Seaside Ampitheatre"),
    ("Chautauqua Theater", "Florida Chautauqua Theatre"),
    ("The Village Door", "John Wehner's Village Door"),
    ("Havana Beach Bar", "Havana Beach Rooftop Bar"),
    ("Havana Beach Bar", "Havana Beach Bar & Grill"),
    ("The Big Chill 30A", "The Big Chill"),
    # GPT-4o Vision reads a flyer's short venue name; sowal.com uses the
    # full "Town Center" form. Same real venue.
    ("Watersound Town Center", "Watersound"),
    # sowal.com uses the official "The Village of Baytowne Wharf" name; the
    # venue's own flyers/Instagram just say "Baytowne Wharf" -- same real
    # venue, confirmed live 2026-09-02 (The Typos, same date/time from both
    # sowal and a venue flyer, produced two cards for one show).
    ("Baytowne Wharf", "The Village of Baytowne Wharf"),

    # Performer spelling/formatting variants (same SoWal port, 2026-07-13).
    ("Coconut Radio", "COCONUT RADIO"),
    ("DJ 30A", "DJ30A"),
    ("DJ Q$", "DJ QS"),
    ("Gilleran's Island", "Gilleran's Island Band"),
    ("Pickled Pickers", "The Pickled Pickers"),
    ("New Cahoots", "The New Cahoots"),
    ("Bill Garrett", "Bill Garrett + John Reinlie @ Brunch / The Typos"),
    ("Bill Garrett", "Bill Garrett + John Reinlie @ Brunch / WineLers"),
    # Pre-dates split_title()'s " at Venue" fallback: an older crawl saved
    # the whole raw title as the performer instead of splitting it, leaving
    # this malformed row sitting next to the correctly re-crawled one for
    # the same sowal.com URL (event ids 981/990/1004/1052/1061 vs
    # 2495/2496/2499/2506/2507, confirmed live 2026-07-22).
    ("Michael Johnson", "Michael Johnson at Havana Beach Bar & Grill"),
    # favorites_watch's OpenAI web_search reported this act's name with a
    # U+2011 non-breaking hyphen ("Martin‑Lane") -- a stylization from an
    # aggregator's event-title slug, not the band's own billing. Confirmed
    # via martinlanemusic.com / facebook.com/MartinLaneMusic: the real name
    # uses a space, "Martin Lane". And unlike "Blues Old Stand (acoustic)"
    # (a distinct stripped-down billing of a normally full band), Martin Lane
    # IS an acoustic duo (Laura Lane + Chip Martin) -- there's no separate
    # "full band" version, so "(acoustic duo)" is redundant description, not
    # a distinct identity. All variants collapse to the one bare name.
    ("Martin Lane", "Martin‑Lane"),
    ("Martin Lane", "Martin Lane (acoustic duo)"),
    ("Martin Lane", "Martin‑Lane (acoustic duo)"),
    # GPT-4o Vision read Shelby's Instagram calendar grid with the duo's
    # first/last words swapped ("Lane Martin" instead of "Martin Lane") --
    # confirmed by the exact same identity (Shelby's Beach Bar, same dates)
    # already on record under the correct name from a separate flyer.
    ("Martin Lane", "Lane Martin"),
    # Found by detect_schedule_conflicts()'s same_night_collision check: same
    # venue, same date, same 7:00 PM billing under two differently-worded
    # titles for what's clearly the one act.
    ("Harrison Prentice", "Harrison Prentice (songwriter)"),
    ("Harrison Prentice", "Songwriter Harrison Prentice"),
    # Same detector, same pattern: identical venue/date/4:00 PM slot under a
    # descriptive event title vs. the act's own name.
    ("Mike Whitty & Friends", "Sunday Pickin' w/ Mike Whitty & Friends"),
    # The act's own promotional calendar bills itself "Lane Maury Music";
    # every other source (venue site, sowal) already uses the bare name.
    ("Lane Maury", "Lane Maury Music"),
]

# variant (lowercased) -> canonical
_VARIANT_TO_CANONICAL: dict[str, str] = {
    variant.strip().lower(): canonical for canonical, variant in CANONICAL_FIXES
}

# Typographic character variants folded to their plain-ASCII equivalent before
# any matching happens. GPT-4o Vision reads stylized flyer text and reports
# "smart" quotes (e.g. "STINKY'S BAIT SHACK" with U+2019) while SoWal's plain
# text uses a straight apostrophe (U+0027) -- same venue, different bytes,
# which silently defeated identity_key matching and produced duplicate events.
_TYPOGRAPHIC_FOLDS = {
    "‘": "'", "’": "'", "ʼ": "'", "´": "'", "`": "'",
    "“": '"', "”": '"',
    "–": "-", "—": "-",
}


def _fold_typography(value: str) -> str:
    for fancy, plain in _TYPOGRAPHIC_FOLDS.items():
        value = value.replace(fancy, plain)
    return value


# Vision sometimes reports a performer/venue fully in caps ("BROOKE WASHOR")
# where SoWal's own text (or a cleaner read of the same image on a different
# day) gives normal title case ("Brooke Washor") -- a lighter general
# companion to the hand-curated CANONICAL_FIXES table above, which only
# catches variants someone has already noticed and added. Only fires when
# the WHOLE value is uppercase, so it can never touch an already-correctly-
# cased name. Short all-caps tokens common in this dataset are preserved
# rather than title-cased into "Dj"/"Tj"/"Aj".
_PRESERVE_UPPER_TOKENS = {"DJ", "TJ", "AJ"}

# str.title() mis-capitalizes the letter right after an apostrophe
# ("STINKY'S" -> "Stinky'S" -- wrong, should stay "Stinky's"), since it
# treats the apostrophe as a fresh word boundary. Patch the common
# contraction/possessive suffixes back down; a genuine new-word case like
# "O'BRIEN" -> "O'Brien" is already correct and untouched by these.
_TITLE_CASE_APOSTROPHE_FIXES = {
    "'S": "'s", "'T": "'t", "'D": "'d", "'M": "'m", "'Ll": "'ll", "'Re": "'re", "'Ve": "'ve",
}


def _fold_all_caps(value: str) -> str:
    if not value.isupper():
        return value
    words = []
    for w in value.split(" "):
        if w in _PRESERVE_UPPER_TOKENS:
            words.append(w)
            continue
        titled = w.title()
        for wrong, right in _TITLE_CASE_APOSTROPHE_FIXES.items():
            titled = titled.replace(wrong, right)
        words.append(titled)
    return " ".join(words)


def canonicalize(value: str | None) -> str | None:
    """
    Return the canonical spelling for a performer/venue value.
    A known variant (CANONICAL_FIXES) always wins. Otherwise, typographic
    quote/dash variants are folded to plain ASCII (see _TYPOGRAPHIC_FOLDS)
    and an all-caps value is title-cased (see _fold_all_caps) so sources
    describing the same venue/performer with different typography or
    capitalisation still collapse to one identity. Any other unknown value
    passes through unchanged.
    """
    if not value:
        return value
    trimmed = _fold_typography(value.strip())
    known = _VARIANT_TO_CANONICAL.get(trimmed.lower())
    if known:
        return known
    return _fold_all_caps(trimmed)


# Venue-aware performer aliases: the same short name refers to a different act
# depending on the venue. Verified against the SoWal events calendar — e.g. at
# Stinky's the residency is billed as the full band, at North Beach Social it is
# the solo act. Keyed on (performer_lower, venue_lower) -> canonical performer.
VENUE_PERFORMER_ALIASES: dict[tuple[str, str], str] = {
    ("dion jones", "stinky's bait shack"): "Dion Jones & The Neon Tears",
}


def apply_venue_alias(performer: str | None, venue: str | None) -> str | None:
    """Resolve a venue-specific performer alias, else return the performer unchanged."""
    if not performer:
        return performer
    key = (performer.strip().lower(), (venue or "").strip().lower())
    return VENUE_PERFORMER_ALIASES.get(key, performer)
